import re
import subprocess
import time
import traceback
import typing
from dataclasses import dataclass, field
from pathlib import Path

from stackview import LocalsViewer, StackViewer


@dataclass
class Node:
    line: int = field(default=0, kw_only=True)
    col: int = field(default=0, kw_only=True)
    _len: typing.ClassVar[int] = 0
@dataclass
class DefineVar(Node):
    name: str
    value: object
    _len: typing.ClassVar[int] = 6
@dataclass
class DefineGlobalVar(Node):
    name: str
    value: object
    _len: typing.ClassVar[int] = 12
@dataclass
class SetVar(Node):
    name: str
    newValue: object
    _len: typing.ClassVar[int] = 3
@dataclass
class SetGlobalVar(Node):
    name: str
    newValue: object
    _len: typing.ClassVar[int] = 9
@dataclass
class DeleteVar(Node):
    name: str
    _len: typing.ClassVar[int] = 6
@dataclass
class DeleteGlobalVar(Node):
    name: str
    _len: typing.ClassVar[int] = 12

@dataclass
class Pop(Node):
    varOptional: str | None
    _len: typing.ClassVar[int] = 3
@dataclass
class Push(Node):
    value: str | object
    _len: typing.ClassVar[int] = 4
@dataclass
class PopLocal(Node):
    varOptional: str | None
    _len: typing.ClassVar[int] = 8
@dataclass
class PushLocal(Node):
    value: str | object
    _len: typing.ClassVar[int] = 9
@dataclass
class Function(Node):
    name: str
    args: list[str]
    body: list
    _len: typing.ClassVar[int] = 8
    defaults: dict[str, object] = field(default_factory=dict)
@dataclass
class BlockEnd(Node):
    _len: typing.ClassVar[int] = 8
@dataclass
class Call(Node):
    func: str 
    args: list
    _usedAsEvent: bool = False
    _len: typing.ClassVar[int] = 4
@dataclass
class InvokeNative(Node):
    name: str
    local: bool = False
    @property
    def _len(self) -> int:
        return 5 if self.local else 10
@dataclass
class Apply(Node):
    func: str
    _len: typing.ClassVar[int] = 5
@dataclass
class ApplyLocal(Node):
    func: str
    _len: typing.ClassVar[int] = 10

@dataclass
class Literal(Node):
    val: object
@dataclass
class Variable(Node):
    name: str
@dataclass
class CollectionLiteral(Node):
    prefix: str  # 'L', 'T' or 'M'
    text: str

@dataclass
class Condition(Node):
    left: object
    op: str
    right: object
@dataclass
class BooleanExpression(Node):
    left: Condition
    op: str 
    right: Condition
@dataclass
class If(Node):
    branches: list
    _len: typing.ClassVar[int] = 2
@dataclass
class BlockExit(Node):
    depth: int = 1
    _len: typing.ClassVar[int] = 4
@dataclass
class Loop(Node):
    body: list
    _len: typing.ClassVar[int] = 4
@dataclass 
class Continue(Node):
    _len: typing.ClassVar[int] = 8

@dataclass
class Return(Node):
    value: object | None
    noExit: bool = False
    _len: typing.ClassVar[int] = 6

@dataclass
class Broadcast(Node):
    message: str
    _len: typing.ClassVar[int] = 9
class _Event:
    def __init__(self, listening: str, line: int = 0):
        self.line = line
        self.listening = listening
        self._handler: Function | None = None
    @property
    def handler(self) -> Function | None:
        return self._handler
    @handler.setter
    def handler(self, value: Function | None):
        self._handler = value

    def __call__(self):
        return Call(line=self.line, func=self._handler.name, args=[], _usedAsEvent=True)

class Program:
    def __init__(self, evaluator: 'Evaluator'):
        self.evaluator = evaluator
        self.nodes = self.evaluator.parse()
class Ref(str):
    """A reference inside collections"""

class _CollectionReader:
    NUMBER = re.compile(r'-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?')
    IDENT = re.compile(r'[A-Za-z_]\w*')
    CLOSERS = {'[': ']', '(': ')', '{': '}'}
    KINDS = {'[': list, '(': tuple, '{': dict, 'L': list, 'T': tuple, 'M': dict}
    ESCAPES = {'n': '\n', 't': '\t', '\\': '\\', '"': '"'}

    def __init__(self, text: str, popHook=None, line: int | None = None, col: int = 1):
        self.text = text
        self.pos = 0
        self.popHook = popHook
        self.originLine = line
        self.originCol = col
    def location(self, pos: int | None = None):
        pos = self.pos if pos is None else pos
        pos = max(0, min(pos, len(self.text) - 1))
        newlines = self.text.count('\n', 0, pos)
        if newlines == 0:
            return self.originLine, self.originCol + pos
        return self.originLine + newlines, pos - self.text.rfind('\n', 0, pos)
    @property
    def line(self):
        return self.location()[0] if self.originLine is not None else None
    @property
    def col(self):
        return self.location()[1] if self.originLine is not None else None
    def locate(self, exc: Exception, pos: int | None = None):
        if self.originLine is not None and not hasattr(exc, '_psLine'):
            exc._psLine, exc._psCol = self.location(pos)
        return exc
    def error(self, msg, pos: int | None = None):
        if self.originLine is None:
            at = self.pos if pos is None else pos
            return ValueError(f"{msg} at position {at} in {self.text!r}")
        return self.locate(ValueError(msg), pos)
    def peek(self):
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1
        return self.text[self.pos] if self.pos < len(self.text) else ''
    def readDocument(self):
        value = self.readValue()
        if self.peek():
            raise self.error('Unexpected trailing text')
        return value
    def readValue(self):
        ch = self.peek()
        if not ch:
            raise self.error('Unexpected end of collection')
        if ch in ('"', "'"):
            return self.readString()
        if ch in self.CLOSERS:
            self.pos += 1
            return self.readItems(self.KINDS[ch], self.CLOSERS[ch])
        if ch in 'LTM' and self.text.startswith('{', self.pos + 1):
            self.pos += 2
            return self.readItems(self.KINDS[ch], '}')

        match = self.NUMBER.match(self.text, self.pos)
        if match:
            self.pos = match.end()
            raw = match.group()
            return float(raw) if any(c in raw for c in '.eE') else int(raw)

        match = self.IDENT.match(self.text, self.pos)
        if match:
            self.pos = match.end()
            name = match.group()
            if name.lower() in ('true', 'false'):
                return name.lower() == 'true'
            if self.popHook is not None and name in ('pop', 'poplocal'):
                try:
                    return self.popHook(name)
                except Exception as e:
                    raise self.locate(e, match.start())
            return Ref(name)

        raise self.error(f'Unexpected character {ch!r}')
    def readString(self):
        start = self.pos
        self.pos += 1  # opening quote
        out = []
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            self.pos += 1
            if ch in ['"', "'"]:
                return ''.join(out)
            if ch == '\\' and self.pos < len(self.text):
                nxt = self.text[self.pos]
                self.pos += 1
                out.append(self.ESCAPES.get(nxt, '\\' + nxt))
            else:
                out.append(ch)
        raise self.error('Unterminated string', start)
    def readItems(self, kind, closer):
        items = []
        while self.peek() != closer:
            if kind is dict:
                key = self.readValue()
                if self.peek() != ':':
                    raise self.error("Expected ':' after map key")
                self.pos += 1
                items.append((key, self.readValue()))
            else:
                items.append(self.readValue())

            nxt = self.peek()
            if nxt == ',':
                self.pos += 1
            elif nxt != closer:
                raise self.error(f"Expected ',' or {closer!r}")
        self.pos += 1  # closer
        try:
            return kind(items)
        except TypeError:
            raise self.error('Unhashable map key')
class Natives:
    local = False
       
    @staticmethod
    def _getStack():
        return LOCAL_STACK[-1] if Natives.local else STACK
    @staticmethod
    def _pop(v):
        return Natives._getStack().pop()
    @staticmethod
    def _push(value):
        Natives._getStack().append(value)
        if len(Natives._getStack()) > ((Config.MAX_STACK_DEPTH if not Natives.local else Config.MAX_LOCAL_STACK_DEPTH) or float('inf')):
            raise RuntimeError("Maximum stack depth exceeded")
    @staticmethod
    def _toPyIter(value):
        if isinstance(value, (list, tuple, dict)):
            return value
        if isinstance(value, str) and value.strip().startswith(('L{', 'T{', 'M{')):
            return _CollectionReader(value).readDocument()
        raise ValueError(f"Invalid collection: {value!r}")

    @staticmethod
    def _encode(item):
        if isinstance(item, (list, tuple, dict)):
            return Natives._toIntIter(item)
        if isinstance(item, bool):
            return 'true' if item else 'false'
        if isinstance(item, Ref):
            return str.__str__(item)
        if isinstance(item, str):
            escaped = (item.replace('\\', '\\\\').replace('"', '\\"')
                            .replace('\n', '\\n').replace('\t', '\\t'))
            return f'"{escaped}"'
        if isinstance(item, float):
            return repr(item)
        return str(item)
    @staticmethod
    def _toIntIter(value):
        if isinstance(value, list):
            prefix, items = 'L', [Natives._encode(i) for i in value]
        elif isinstance(value, tuple):
            prefix, items = 'T', [Natives._encode(i) for i in value]
        elif isinstance(value, dict):
            prefix = 'M'
            items = [f'{Natives._encode(k)}:{Natives._encode(v)}' for k, v in value.items()]
        else:
            raise TypeError(f"Cannot serialize {type(value).__name__} as a collection")
        return f"{prefix}{{{','.join(items)}}}"
    @staticmethod
    def _coerce(value):
        if isinstance(value, str) and value.startswith(('L{', 'T{', 'M{')) and value.endswith('}'):
            try:
                return Natives._toPyIter(value)
            except ValueError:
                pass
        return value
    @staticmethod
    def _pushElement(v, element):
        if isinstance(element, Ref):
            element = v.getVar(str.__str__(element))
        if isinstance(element, (list, tuple, dict)):
            element = Natives._toIntIter(element)
        Natives._push(element)

    @staticmethod
    def add(v):
        a = Natives._pop(v)
        b = Natives._pop(v)
        Natives._push(a + b)
    @staticmethod
    def sub(v):
        a = Natives._pop(v)
        b = Natives._pop(v)
        Natives._push(b - a)
    @staticmethod
    def mul(v):
        a = Natives._pop(v)
        b = Natives._pop(v)
        Natives._push(a * b)
    @staticmethod
    def intdiv(v):
        a = Natives._pop(v)
        b = Natives._pop(v)
        Natives._push(b // a)
    @staticmethod
    def div(v):
        a = Natives._pop(v)
        b = Natives._pop(v)
        Natives._push(b / a)

    @staticmethod
    def print(v):
        print(Natives._getStack()[-1])
    @staticmethod
    def printWithPop(v):
        print(Natives._pop(v))

    @staticmethod
    def dup(v):
        a = Natives._pop(v)
        Natives._push(a)
        Natives._push(a)
    @staticmethod
    def swap(v):
        a = Natives._pop(v)
        b = Natives._pop(v)
        Natives._push(a)
        Natives._push(b)

    @staticmethod
    def over(v):
        Natives._push(Natives._getStack()[-2])
    @staticmethod
    def rot(v):
        a = Natives._pop(v)
        b = Natives._pop(v)
        c = Natives._pop(v)
        Natives._push(b)
        Natives._push(a)
        Natives._push(c)
    @staticmethod
    def nip(v):
        Natives._getStack().pop(-2)
    @staticmethod
    def tuck(v):
        a = Natives._pop(v)
        b = Natives._pop(v)
        Natives._push(a)
        Natives._push(b)
        Natives._push(a)
    @staticmethod
    def depth(v):
        Natives._push(len(Natives._getStack()))
    @staticmethod
    def pick(v):
        n = Natives._pop(v)
        if len(Natives._getStack()) <= n:
            raise RuntimeError("Index out of bounds")
        Natives._push(Natives._getStack()[-1 - n])
    @staticmethod
    def roll(v):
        n = Natives._pop(v)
        stack = Natives._getStack()
        if len(stack) <= n:
            raise RuntimeError("Index out of bounds")
        stack[-1 - n], stack[-1] = stack[-1], stack[-1 - n]

    @staticmethod
    def mod(v):
        a = Natives._pop(v); b = Natives._pop(v)
        Natives._push(b % a)
    @staticmethod
    def neg(v):
        Natives._push(-Natives._pop(v))
    @staticmethod
    def abs(v):
        Natives._push(abs(Natives._pop(v)))
    @staticmethod
    def min(v):
        a = Natives._pop(v); b = Natives._pop(v)
        Natives._push(min(a, b))
    @staticmethod
    def max(v):
        a = Natives._pop(v); b = Natives._pop(v)
        Natives._push(max(a, b))
    @staticmethod
    def pow(v):
        a = Natives._pop(v); b = Natives._pop(v)
        Natives._push(b ** a)
    @staticmethod
    def inc(v):
        Natives._push(Natives._pop(v) + 1)
    @staticmethod
    def dec(v):
        Natives._push(Natives._pop(v) - 1)
    @staticmethod
    def round(v):
        d = Natives._pop(v)
        a = Natives._pop(v)
        Natives._push(round(a, d)) 

    @staticmethod
    def strconcat(v):
            a = Natives._pop(v)
            b = Natives._pop(v)
            Natives._push(str(b) + str(a))
    @staticmethod
    def strlen(v):
        Natives._push(len(str(Natives._pop(v))))
    @staticmethod
    def substr(v):
        length = Natives._pop(v)
        start = Natives._pop(v)
        s = str(Natives._pop(v))
        Natives._push(s[start:start+length])
    @staticmethod
    def striterjoin(v):
        sep = str(Natives._pop(v))
        lst = Natives._toPyIter(Natives._pop(v))
        Natives._push(sep.join(map(str, lst)))
    @staticmethod
    def strsplit(v):
        sep = Natives._pop(v)
        s = str(Natives._pop(v))
        Natives._push(Natives._toIntIter(s.split(sep)))
    @staticmethod
    def strfind(v):
        sub = Natives._pop(v)
        s = str(Natives._pop(v))
        Natives._push(s.find(sub))

    @staticmethod
    def iterlen(v):
        lst = Natives._toPyIter(Natives._pop(v))
        Natives._push(len(lst))
    @staticmethod
    def getelement(v):
        idx = Natives._pop(v)
        lst = Natives._toPyIter(Natives._pop(v))
        Natives._pushElement(v, lst[idx])
    @staticmethod
    def setelement(v):
        newVal = Natives._coerce(Natives._pop(v))
        idx = Natives._pop(v)
        lst = Natives._toPyIter(Natives._pop(v))
        lst[idx] = newVal
        Natives._push(Natives._toIntIter(lst))
    @staticmethod
    def iterappend(v):
        val = Natives._coerce(Natives._pop(v))
        lst = Natives._toPyIter(Natives._pop(v))
        lst.append(val)
        if len(lst) > (Config.MAX_ITERABLE_SIZE or float('inf')):
            raise RuntimeError("Maximum iterable size exceeded")
        Natives._push(Natives._toIntIter(lst))
    @staticmethod
    def iterclear(v):
        lst = Natives._toPyIter(Natives._pop(v))
        lst.clear()
        Natives._push(Natives._toIntIter(lst))
    @staticmethod
    def delelement(v):
        idx = Natives._pop(v)
        itr = Natives._toPyIter(Natives._pop(v))
        del itr[idx]

        Natives._push(Natives._toIntIter(itr))
    @staticmethod
    def iterextend(v):
            extd = Natives._toPyIter(Natives._pop(v))
            lst = Natives._toPyIter(Natives._pop(v))
            lst.extend(extd)
            if len(lst) > (Config.MAX_ITERABLE_SIZE or float('inf')):
                raise RuntimeError("Maximum iterable size exceeded")
            Natives._push(Natives._toIntIter(lst))
    @staticmethod
    def sort(v):
        try:
            reverse = (Natives._pop(v) == 1)
        except IndexError:
            reverse = False
        lst = Natives._toPyIter(Natives._pop(v))
        Natives._push(Natives._toIntIter(sorted(lst, reverse=reverse)))
    @staticmethod
    def iterpop(v):
        try:
            idx = Natives._pop(v)
        except Exception:
            idx = -1
        if not isinstance(idx, int):
            Natives._push(idx)
            idx = -1
        lst = Natives._toPyIter(Natives._pop(v))
        Natives._pushElement(v, lst.pop(idx))
        Natives._push(Natives._toIntIter(lst))

    @staticmethod
    def totuple(v):
        lst = Natives._toPyIter(Natives._pop(v))
        Natives._push(Natives._toIntIter(tuple(lst)))
    @staticmethod
    def tolist(v):
        lst = Natives._toPyIter(Natives._pop(v))
        Natives._push(Natives._toIntIter(list(lst)))

    @staticmethod 
    def dump(v):
        for i in Natives._getStack():
            print(i)

    @staticmethod 
    def dictgetvalue(v):
        try:
            default = Natives._pop(v)
        except Exception:
            default = None
        if not isinstance(default, int):
            Natives._push(default)
            default = None
        key = Natives._pop(v)
        dct = Natives._toPyIter(Natives._pop(v))
        Natives._pushElement(v, dct.get(key, default))
    @staticmethod
    def dictsetvalue(v):
        val = Natives._pop(v)
        key = Natives._pop(v)
        dct = Natives._toPyIter(Natives._pop(v))
        dct[key] = val

        if len(dct) > (Config.MAX_ITERABLE_SIZE or float('inf')):
            raise RuntimeError("Maximum iterable size exceeded")
        Natives._pushElement(v, dct)
    @staticmethod 
    def dictkeys(v):
        dct = Natives._toPyIter(Natives._pop(v))
        Natives._pushElement(v, list(dct.keys()))
    @staticmethod 
    def dictvalues(v):
        dct = Natives._toPyIter(Natives._pop(v))
        Natives._pushElement(v, list(dct.values()))

    @staticmethod
    def sleep(v):
        seconds = Natives._pop(v)
        if not isinstance(seconds, (int, float)):
            raise TypeError("sleep expects a number of seconds")
        time.sleep(seconds)
    @staticmethod
    def raiseerror(v):
        message = Natives._pop(v)
        raise RuntimeError(message)
    @staticmethod
    def asserterror(v):
        message = Natives._pop(v)
        condition = Natives._pop(v)
        assert condition, message

NATIVES = {
    name: getattr(Natives, name)
    for name in dir(Natives)
    if not name.startswith('_') and callable(getattr(Natives, name))
}

KEYWORDS = [
    'define', 'defineglobal', 'pop', 'push', 
    'function', 'end', 'call', 'set', 'setglobal', 
    'callnative', 'if', 'elif', 'else', 'poplocal', 'pushlocal', 
    'calllocalnative', 'exit', 'loop', 'import', 
    'apply', 'applylocal', 'return', 'continue',
    'event', 'broadcast', 'delete', 'deleteglobal'
]
TOKEN_PATTERN = re.compile(
    r'//[^\n]*'
    r'|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''
    r'|-?\d+(?:\.\d+)?'
    r'|[A-Za-z_]\w*'
    r'|==|!=|<=|>=|&&|\|\|'
    r'|[()\[\],]'
    r'|[ \t]+'
    r'|\n'
    r'|.'
)
PRECEDENCE = {
    '||': 1,
    '&&': 2,
    '==': 3,
    '!=': 3,
    '<': 3,
    '>': 3,
    '<=': 3,
    '>=': 3,
}
BASE_DIR = Path(__file__).parent

EVENTS: dict[str, list[_Event]] = {}
FUNCTIONS: dict[str, Function] = {}
IMPORTING_FILES: set[Path] = set()
IMPORTED_FILES: set[Path] = set()

_function_origination: dict[str, str] = {}
_function_profiles: dict[str, tuple[int | float, int | float]] = {}

class Evaluator:
    def __init__(self, fileName: str = '', forcePath: Path | None = None, package: str = ''):
        try:
            if not forcePath:
                with open(BASE_DIR / 'scripts' / f'{fileName}.txt', 'r') as f:
                    self.source = f.read()
                    f.close()
            else:
                with open(forcePath) as f:
                    self.source = f.read()
                    f.close()
        except (FileNotFoundError, OSError):
            self.source = fileName
        self._plaintext = self.source
        
        self.pos = 0
        self.line = 1
        self.col = 1

        self.package = package
        self.tokens: list[tuple[str, str]] = []
        try:
            self._lexSource()
        except Exception as e:
            print(f"""
Lexer Error
Module: {self.package if self.package else '<self>'}
Line: {self.line}, Column: {self.col}

Error: 
[{type(e).__name__}] {e}
                        """)
            _text_A = self._plaintext.splitlines()[self.line - 1]
            _text_B = self._plaintext.splitlines()[self.line - 1].lstrip()
            print(_text_B)
            print(' ' * (self.col - 1 - (len(_text_A) - len(_text_B))) + '^')
            if Config.SHOW_PYTHON_TRACEBACK:
                traceback.print_exc()

            raise SystemExit(1)
    def _lexSource(self):
        chars = []
        in_string = False
        in_comment = False
        escaped = False

        for char in self.source:
            if in_comment:
                if char == '\n':
                    in_comment = False
                    chars.append(char)
                else:
                    chars.append(' ')
                continue

            if in_string:
                chars.append(char)
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char in ('"', "'"):
                    in_string = False
                continue

            if char in ('"', "'"):
                in_string = True
                chars.append(char)
            elif char == ';':
                in_comment = True
                chars.append(' ')
            else:
                chars.append(char)

        self.source = ''.join(chars)
        tokens = []
        context = ''
        matches = iter(TOKEN_PATTERN.finditer(self.source))
        skip_until = 0
        lineNo = 1
        lastPos = 0

        for match in matches:
            if Config.DEBUG_PARSER:
                print('Lexer match:', match)
            if match.start() < skip_until:
                continue
            value = match.group()

            lineNo += self.source.count('\n', lastPos, match.start())
            lastPos = match.start()
            self.line = lineNo

            line_start = self.source.rfind('\n', 0, match.start()) + 1
            column = match.start() - line_start + 1
            self.col = column
        
            if value in ['[', '(', '{']:
                depth = 1
                pos = match.end()

                if context in ('funcdef', 'ifdef'):
                    tokens.append(('PUNCTUATION', value, self.col, self.line))
                    continue

                if value == '[':
                    closing = ']'
                    _type = 'LIST'
                elif value == '(':
                    closing = ')'
                    _type = 'TUPLE'
                elif value == '{':
                    closing = '}'
                    _type = 'MAP'
                
                in_str = False
                nested = 0
                has_top_level_comma = False
                openers = '([{'
                closers = ')]}'
                while pos < len(self.source) and depth > 0:
                    c = self.source[pos]
                    if in_str:
                        if c == '\\':
                            pos += 1
                        elif c == '"':
                            in_str = False
                    elif c == '"':
                        in_str = True
                    elif c == value:
                        depth += 1
                    elif c == closing:
                        depth -= 1
                    elif c in openers:
                        nested += 1
                    elif c in closers:
                        nested = max(0, nested - 1)
                    elif c == ',' and depth == 1 and nested == 0:
                        has_top_level_comma = True
                    pos += 1
                if depth != 0:
                    raise SyntaxError(f"Unclosed {_type.lower()}")

                body = self.source[match.end():pos - 1]
                if value == '(' and not has_top_level_comma and body.strip():
                    tokens.append(('PUNCTUATION', value, self.col, self.line))
                    self.col = self.col
                    continue

                if Config.NO_ITERABLE:
                    raise SyntaxError("Iterables are not allowed by bootflag")

                tokens.append((_type, self.source[match.end():pos - 1], self.col, self.line))
                skip_until = pos
                continue
            elif value.startswith('//'):
                pos = self.source.find('\n', match.start())

                if pos == -1:
                    break

                skip_until = pos
                continue
            elif value == "\n":
                if context == 'valuedef':
                    context == ''
                tokens.append(("NEWLINE", value, self.col, self.line))
            elif value.startswith(('"', "'")):
                tokens.append(("STRING", value[1:-1], self.col, self.line))
            elif value == '=' and context == 'funcdef':
                tokens.append(("OPERATOR", value, self.col, self.line))
            elif (value.isdigit() or value.startswith('-')) or (value.count(".") == 1 and value.replace(".", "").isdigit()):
                number = float(value) if "." in value else int(value)
                tokens.append(("NUMBER", number, self.col, self.line))
            elif value in KEYWORDS:
                if value == 'function':
                    context = 'funcdef'
                elif value == 'if':
                    context = 'ifdef'
                elif value in ['push', 'pop', 'pushlocal', 'poplocal', 'define', 'defineglobal', 'set', 'setglobal']:
                    context = 'valuedef'
                elif value == 'end':
                    context = ''
                tokens.append(("KEYWORD", value, self.col, self.line))
            elif value[0].isalpha() or value[0] == "_":
                tokens.append(("IDENTIFIER", value, self.col, self.line))
            elif value in ['==', '>=', '<=', '!=', '>', '<']:
                tokens.append(("OPERATOR", value, self.col, self.line))
            elif value in ['&&', '||']:
                tokens.append(("BOOLOP", value, self.col, self.line))
            elif value in "~&^|":
                tokens.append(("BINOP", value, self.col, self.line))
            elif value in "()[],":
                tokens.append(("PUNCTUATION", value, self.col, self.line))
            elif value.isspace():
                pass
            else:
                raise SyntaxError(f"Unexpected character: {value!r}")
            if Config.DEBUG_PARSER:
                print('Lexer value:', value)
        self.tokens = tokens
        if Config.DUMP_TOKENS:
            for token in tokens:
                print(token)

    def parseExpression(self, min_precedence=0):
        left = self.parseSingular()

        while True:
            next_tok = self.peek()
            if not next_tok or next_tok[0] not in ['OPERATOR', 'BOOLOP']:
                break

            operator = next_tok[1]
            try:
                precedence = PRECEDENCE[operator.upper()]
            except KeyError:
                raise SyntaxError(f"Unknown operator: {operator!r}")

            if precedence < min_precedence:
                break

            self.advance()
            right = self.parseExpression(precedence + 1)

            if operator.upper() in ('&&', '||'):
                left = BooleanExpression(left, operator, right, line=next_tok[3])
            else:
                left = Condition(left, operator, right, line=next_tok[3])

        return left
    def _collectionError(self, tok, e: ValueError):
        err = SyntaxError(f"Invalid {tok[0].lower()} literal: {e}")
        for attr in ('_psLine', '_psCol'):
            if hasattr(e, attr):
                setattr(err, attr, getattr(e, attr))
        return err
    def parseSingular(self):
        tok = self.advance()

        if tok[0] == 'KEYWORD' and tok[1] in ('pop', 'poplocal'):
            if tok[1] == 'pop':
                return Pop(None, line=tok[3], col=tok[2])
            return PopLocal(None, line=tok[3], col=tok[2])
        elif tok[0] == 'IDENTIFIER' and tok[1].lower() in ('true', 'false', 'null'):
            value = tok[1].lower()
            if value == 'true':
                return Literal(True, line=tok[3], col=tok[2])
            elif value == 'false':
                return Literal(False, line=tok[3], col=tok[2])
            return Literal(None, line=tok[3], col=tok[2])
        elif tok[0] == 'IDENTIFIER':
            return Variable(tok[1], line=tok[3], col=tok[2])
        elif tok[0] in ('NUMBER', 'STRING'):
            if tok[0] == 'STRING' and tok[1].startswith('<FunctionInstance ') and tok[1].endswith('>'):
                raise RuntimeError("Illegal string expression")
            if tok[0] == 'STRING' and len(str(tok[1])) > (Config.MAX_STRING_SIZE or float('inf')):
                raise RuntimeError("Maximum string length exceeded")
            if tok[0] == 'NUMBER' and Config.MAX_INTEGER is not None and abs(int(tok[1])) > Config.MAX_INTEGER:
                    raise RuntimeError("Maximum integer value exceeded") 
            return Literal(tok[1], line=tok[3], col=tok[2])
        elif tok[0] in ('LIST', 'TUPLE', 'MAP'):
            prefix = tok[0][0]
            found_pop = []
            def noteMarker(name):
                found_pop.append(name)
                return 0
            try:
                # Text index 0 is the synthetic prefix; the body starts one column after the opener
                _CollectionReader(f'{prefix}{{{tok[1]}}}', noteMarker, tok[3], tok[2] - 1).readDocument()
            except ValueError as e:
                raise self._collectionError(tok, e) from e
            if found_pop:
                return CollectionLiteral(prefix, tok[1], line=tok[3], col=tok[2])
            try:
                return Literal(
                    Natives._toIntIter(
                        Natives._toPyIter(f'{tok[0][0]}{{{tok[1]}}}')
                    ),
                    line=tok[3], col=tok[2]
                )
            except ValueError as e:
                raise self._collectionError(tok, e) from e
        elif tok[1] == '(':
            expression = self.parseExpression()
            closing = self.advance()
            if closing[1] != ')':
                raise SyntaxError(f"Expected a closing parenthesis, got {closing[1]!r}")
            return expression
        
        raise SyntaxError(f"Unexpected token: {tok}")
    
    def parseCondition(self):
        condition = self.parseExpression()

        if not isinstance(condition, (BooleanExpression, Condition, Pop, PopLocal, Variable, Literal, CollectionLiteral)):
            raise TypeError("Expected a comparison condition")
        if self.peek() and self.peek()[0] == 'NEWLINE':
            self.advance()

        return condition
    def parseBlock(self, stopKeywords):
        body = []

        while self.peek() is not None:
            if self.peek()[0] == 'NEWLINE':
                self.advance()
                continue

            if (
                self.peek()[0] == 'KEYWORD'
                and self.peek()[1] in stopKeywords
            ):
                break

            body.append(self.parseStatement())

        return body
    def parseIf(self, tok):
        branches = []

        condition = self.parseCondition()
        body = self.parseBlock({'elif', 'else', 'end'})
        branches.append((condition, body))

        while (
            self.peek()
            and self.peek()[0] == 'KEYWORD'
            and self.peek()[1] == 'elif'
        ):
            self.advance()
            condition = self.parseCondition()
            body = self.parseBlock({'elif', 'else', 'end'})
            branches.append((condition, body))

        if (
            self.peek()
            and self.peek()[0] == 'KEYWORD'
            and self.peek()[1] == 'else'
        ):
            self.advance()

            if self.peek() and self.peek()[0] == 'NEWLINE':
                self.advance()

            body = self.parseBlock({'end'})
            branches.append((True, body)) # Just pass a primitive True, so it will run if everything else fails

        if not self.peek() or self.peek()[1] != 'end':
            raise SyntaxError("Expected 'end' to close if statement")

        self.advance()
        return If(branches, line=tok[3], col=tok[2])
    def parseStatement(self):
        tok = self.advance()
        self.col = tok[2]
        if Config.DEBUG_PARSER:
            print('Parsed token:', tok)
        if tok[0] == 'KEYWORD':
            keywordtok = tok[1]

            if keywordtok in ['define', 'defineglobal']:
                name = self.advance()
                if not isinstance(name, tuple) or name[0] != 'IDENTIFIER':
                    raise SyntaxError("Expected a name after 'define' or 'defineglobal'")
                name = name[1]
                value = self.parseExpression()
                if keywordtok == 'define':
                    return DefineVar(name, value, line=tok[3], col=tok[2])
                return DefineGlobalVar(name, value, line=tok[3], col=tok[2])
            elif keywordtok in ['pop', 'poplocal']:
                varOptional = None
                if self.peek() and self.peek()[0] == 'IDENTIFIER':
                    varOptional = self.advance()[1]
                
                if keywordtok == 'pop':
                    return Pop(varOptional, line=tok[3], col=tok[2])
                return PopLocal(varOptional, line=tok[3], col=tok[2])
            elif keywordtok in ['push', 'pushlocal']:
                value = self.parseExpression()
                if not value:
                    raise SyntaxError("Expected a value after 'push' or 'pushlocal'")

                if keywordtok == 'push':
                    return Push(value, line=tok[3], col=tok[2])
                return PushLocal(value, line=tok[3], col=tok[2])
            elif keywordtok in ['set', 'setglobal']:
                name = self.advance()
                if not isinstance(name, tuple) or name[0] != 'IDENTIFIER':
                    raise SyntaxError("Expected a name after 'set' or 'setglobal'")
                name = name[1]
                value = self.parseExpression()

                if keywordtok == 'set':
                    return SetVar(name, value, line=tok[3], col=tok[2])
                return SetGlobalVar(name, value, line=tok[3], col=tok[2])
            elif keywordtok == 'call':
                func = self.advance()[1]
                args = []

                while self.peek() is not None and self.peek()[0] != 'NEWLINE':
                    args.append(self.parseExpression())

                return Call(func, args, line=tok[3], col=tok[2])
            elif keywordtok in ['apply', 'applylocal']:
                func = self.advance()[1]
                if keywordtok == 'apply':
                    return Apply(func, line=tok[3], col=tok[2])
                return ApplyLocal(func, line=tok[3], col=tok[2])
            elif keywordtok == 'function':
                if Config.NO_FUNCTIONS:
                    raise RuntimeError("Function definitions are disabled by bootflag")

                if len(FUNCTIONS) >= (Config.MAX_FUNCTIONS or float('inf')):
                    raise RuntimeError("Maximum number of functions exceeded")

                name = self.advance()[1]
                if self.advance()[1] != '(':
                    raise SyntaxError("Expected '(' after function name")

                args = []
                defaults = dict()
                while self.peek() and self.peek()[1] != ')':
                    arg = self.advance()
                    if arg[0] == 'IDENTIFIER':
                        args.append(arg[1])
                        if args[-1] in args[:-1]:
                            raise SyntaxError("Duplicate argument name")
                    elif arg[1] == '=':
                        if args[-1] in defaults:
                            raise SyntaxError("Duplicate default value for argument")
                        if self.tokens[self.pos-2][0] != 'IDENTIFIER':
                            raise SyntaxError("Expected argument name before default value")

                        value = self.parseSingular()
                        defaults[args[-1]] = value
                    elif arg[1] != ',':
                        raise SyntaxError("Unexpected token in function arguments")

                if not self.peek():
                    raise SyntaxError("Expected ')' after function arguments")
                self.advance()

                body = self.parseBlock({'end'})

                if not self.peek():
                    raise SyntaxError(f"Function '{name}' is missing 'end'")

                self.advance()
                func = Function(name, args, body, defaults=defaults, line=tok[3], col=tok[2])
                FUNCTIONS[name] = func
                if self.package:
                    _function_origination[name] = self.package

                return None
            elif keywordtok in ['callnative', 'calllocalnative']:
                name = self.advance()[1]

                return InvokeNative(name, (keywordtok == 'calllocalnative'), line=tok[3], col=tok[2])
            elif keywordtok == 'end':
                raise SyntaxError("'end' without a matching block")
            elif keywordtok == 'if':
                return self.parseIf(tok)
            elif keywordtok == 'exit':
                try:
                    depth = self.parseExpression()
                except SyntaxError:
                    depth = 1
                if isinstance(depth, Literal) and str(depth.val).isnumeric():
                    return BlockExit(depth.val, line=tok[3], col=tok[2])
                return BlockExit(line=tok[3], col=tok[2])
            elif keywordtok == 'loop':
                body = self.parseBlock({'end'})
                self.advance()
                return Loop(body, line=tok[3], col=tok[2])
            elif keywordtok == 'import':
                module = self.advance()
                if module is None or module[0] != 'IDENTIFIER':
                    raise SyntaxError("Expected module name after 'import'")
                path = (BASE_DIR / 'packages' / f'{module[1]}.txt').resolve()
                if path in IMPORTING_FILES:
                    raise ImportError(f"Circular import detected: {module[1]}")
                if path not in IMPORTED_FILES:
                    if not path.is_file():
                        raise FileNotFoundError(
                            f"Stacky module not found: {path}"
                        )
                    IMPORTING_FILES.add(path)
                    try:
                        start_time = time.time()
                        imported_nodes = Evaluator(forcePath=path, package=module[1]).parse()

                        for imported_node in imported_nodes:
                            if not isinstance(imported_node, (Function, Import)):
                                raise TypeError(
                                    f"Imported module '{module[1]}' "
                                    "can only contain function definitions and imports"
                                )
                        end_time = time.time()
                        print(f'Successfully imported package \'{module[1]}\' in {round(1000*(end_time-start_time),2)}ms')
                        IMPORTED_FILES.add(path)
                    finally:
                        IMPORTING_FILES.remove(path)

                return None
            elif keywordtok == 'return':
                value = None
                if self.peek() is not None and self.peek()[0] != 'NEWLINE':
                    value = self.parseSingular()
                try:
                    noExit = self.parseSingular()
                except SyntaxError:
                    noExit = Literal(False)

                if not isinstance(noExit, Literal):
                    raise TypeError(f"Expected a simple boolean value for noExit flag, got {noExit}")

                return Return(value, noExit, line=tok[3], col=tok[2])
            elif keywordtok == 'continue':
                return Continue(line=tok[3], col=tok[2])
            elif keywordtok in ['event', 'broadcast']:
                if Config.NO_EVENTS:
                    raise RuntimeError("Events are disabled via bootflag")
                if keywordtok == 'event':
                    message = self.parseSingular()
                    if not isinstance(message, Literal) and not isinstance(message.val, str):
                        raise SyntaxError(f"Expected a string for the event handler name, got {message}")
                    body = self.parseBlock({'end'})

                    if not self.peek():
                        raise SyntaxError(f"Event listener '{message.val}' is missing 'end'")
                    self.advance()
                    EVENTS.setdefault(message.val, []).append(_Event(message.val, tok[3]))
                    EVENTS[message.val][-1].handler = Function(body=body, name=message.val, args=[], line=tok[3], col=tok[2])
                    return None
                elif keywordtok == 'broadcast':
                    message = self.parseSingular()
                    if not isinstance(message, Literal) and not isinstance(message.val, str):
                        raise SyntaxError(f"Expected a string for message, got {message}")
                    return Broadcast(message.val, line=tok[3], col=tok[2])
            elif keywordtok in ['delete', 'deleteglobal']:
                name = self.parseSingular()
                if not isinstance(name, Variable):
                    raise SyntaxError(f"Expected a variable, got {name}")
                if keywordtok == 'delete':
                    return DeleteVar(name.name, line=tok[3], col=tok[2])
                return DeleteGlobalVar(name.name, line=tok[3], col=tok[2])
            raise SyntaxError(f"Unknown keyword '{keywordtok}'")
        elif tok[0] in ["NUMBER", "STRING", 'OPERATOR', 'BINOP', 'BOOLOP']:
            pass
        else:
            raise SyntaxError(f"Unexpected token: {tok}")

    def peek(self):
        if self.pos >= len(self.tokens):
            return None
        return self.tokens[self.pos]
    def advance(self):
        tok = self.peek()
        if tok is not None:
            self.pos += 1
        self.col = tok[2] if tok is not None else self.col
        if tok is not None:
            self.line = tok[3]

        if tok is None:
            raise EOFError('Reached unexpected EOF')

        if tok[0] == 'NEWLINE':
            self.line = tok[3] + 1
            self.col = 0

        return tok
    def parse(self):
        self.pos = 0
        nodes = []

        self.line = 1
        self.col = 0

        while self.peek() is not None:
            if self.peek()[0] == 'NEWLINE':
                self.advance()
                continue
            
            try:
                parsed = self.parseStatement()
                if not parsed:
                    continue
                nodes.append(parsed)
            except Exception as e:
                _line = getattr(e, '_psLine', self.line)
                _col = getattr(e, '_psCol', self.col)
                print(f"""
Parsing Error
Module: {self.package if self.package else '<self>'}
Line: {_line}, Column: {_col}
Last Registered Token: {self.peek()}

Error:
[{type(e).__name__}] {e}
                """)
                _text_A = self._plaintext.splitlines()[_line-1]
                _text_B = self._plaintext.splitlines()[_line-1].lstrip()

                print(_text_B)
                print(' ' * (_col - 1 - len(_text_A) + len(_text_B)) + '^')

                if Config.SHOW_PYTHON_TRACEBACK:
                    traceback.print_exc()

                raise SystemExit(1)

        return nodes

GLOBAL_VAR: dict[str, object] = {}
STACK: list[object] = []
LOCAL_VAR_STACK: list[dict[str, object]] = []
LOCAL_STACK: list[list[object]] = []

primitive = (int, str, bool)
class Config:
    MAX_RECURSION_DEPTH: int = 1024
    MAX_STACK_DEPTH: int | None = None
    MAX_LOCAL_STACK_DEPTH: int | None = None
    MAX_STACK_FRAMES: int | None = None
    
    MAX_INSTRUCTION_COUNT: int | None = None
    MAX_FUNCTIONS: int | None = None
    MAX_VARIABLES: int | None = None
    MAX_LOCAL_VARIABLES: int | None = None
    
    MAX_ITERABLE_SIZE: int | None = None
    MAX_STRING_SIZE: int | None = None
    MAX_INTEGER: int = 2**64

    DEBUG: bool = False
    DEBUG_PARSER: bool = False
    DEBUG_SCOPE: bool = False
    SHOW_PYTHON_TRACEBACK: bool = False

    NO_NATIVES: bool = False
    NO_FUNCTIONS: bool = False
    NO_IMPORTS: bool = False
    NO_RECURSION: bool = False
    NO_LOOP: bool = False
    NO_VAR_MODIFY: bool = False
    NO_ITERABLE: bool = False
    NO_EVENTS: bool = False

    LOG_EVENTS: bool = False

    PRINT_ON_LAUNCH: str = ''
    CLEAR_ON_LAUNCH: bool = False

    DUMP_CONFIG: bool = False
    DUMP_AST: bool = False
    DUMP_FUNCTIONS: bool = False
    DUMP_FULL_FUNCTIONS: bool = False
    DUMP_EVENTS: bool = False
    DUMP_NATIVES: bool = False
    DUMP_TOKENS: bool = False

    STOP_AT_STEP: int | None = None
    EXECUTION_MS_LIMIT: int = 5

    PROFILE_FUNCTIONS: bool = False
    PROFILE_VARIABLES: bool = False
    SUMMARIZE_FUNC_PROFILES: bool = False

    AUTO_FILE: str = ''
    ALLOW_MANUAL_FLAG_ENTRY: bool = False
    ALLOW_FLAGS: bool = True

with open(BASE_DIR / 'bootflags.txt', 'r') as f:
    config_lines = f.readlines()
def parseKey(line: str):
    if not Config.ALLOW_FLAGS:
        raise ValueError("Bootflags are not allowed.")

    line = line.replace('-', '_')
    key, value = line.split('=', 1)
    key = key.strip().upper()
    value = value.strip()
    if hasattr(Config, key):
        attr = getattr(Config, key)
        attr_type = type(attr)
        if attr_type is bool:
            setattr(Config, key, value.lower() == 'true')
        elif attr_type is int:
            setattr(Config, key, int(value))
        else:
            setattr(Config, key, value)
        if getattr(Config, key) == attr:
            print(f'Nothing changed. {key} was already set to {attr}')
            return
    else:
        raise ValueError(f"Unknown bootflag: {key}")
    print(f"Bootflag {key} set to {getattr(Config, key)}")
_start_config = time.time()
for line in config_lines:
    if not line or line.startswith('#'):
        continue
    if '=' not in line:
        print('Read through a bootflag without an assignment:', line)
        continue
    parseKey(line)
_end_config = time.time()
if Config.DUMP_CONFIG:
    for key in dir(Config):
        if not key.startswith('_'):
            print(f"{key} = {getattr(Config, key)}")
print(f"All bootflags loaded in {round(1000*(_end_config-_start_config),2)}ms")

if Config.ALLOW_MANUAL_FLAG_ENTRY:
    print("Manual bootflag entry enable. Newline to finish")
    while True:
        user_input = input("Enter bootflag (KEY=VALUE): ")
        if user_input.lower().isspace() or not user_input:
            break
        try:
            parseKey(user_input)
        except Exception as e:
            print(f"Error parsing bootflag: {e}")

step_counter = 0
FUNCTION_PROF_START: float = 0.0
class Interpreter:
    def __init__(self, prog: Program):
        self.prog = prog
        self.running = False
        self.nodes = prog.nodes
        self.pos = -1
        self.scope = 'GLOBAL'
        self.scopes_stack: list[str] = []

        self.stack_frames: list[dict] = []

        self.currentnode: Node | None = None

        self._internal_CONVERT_FUNCTION = Function

    def getVar(self, name, context=None, precedence='global'):
        if context is None:
            context = {}

        _start = time.time()
        if precedence == 'local' and self.scope != 'GLOBAL':
            if name in context:
                _ret = context[name]
            if LOCAL_VAR_STACK and name in LOCAL_VAR_STACK[-1]:
                _ret = LOCAL_VAR_STACK[-1][name]
            if '_ret' in locals():
                _end = time.time()
                if Config.PROFILE_VARIABLES:
                    print(f"Resolved variable '{name}' in {round(1000*(_end-_start),2)}ms")
                    print(f"with value {_ret}")
                return _ret

        if name in GLOBAL_VAR:
            _ret = GLOBAL_VAR[name]
        elif name in FUNCTIONS:
            _ret = FUNCTIONS[name]
        elif self.scope != 'GLOBAL':
            if name in context:
                _ret = context[name]
            if LOCAL_VAR_STACK and name in LOCAL_VAR_STACK[-1]:
                _ret = LOCAL_VAR_STACK[-1][name]

        if '_ret' in locals():
            _end = time.time()
            if Config.PROFILE_VARIABLES:
                print(f"Resolved variable '{name}' in {round(1000*(_end-_start),2)}ms")
                print(f"with value {_ret}")
            return _ret
        raise NameError(f"Variable '{name}' is not defined")

    def _resolveList(self, internalList: list | tuple, scope: typing.Literal['local', 'global'], context):
        if scope == 'local':
            _vars = LOCAL_VAR_STACK[-1]
        else:
            _vars = GLOBAL_VAR

        if isinstance(internalList, tuple):
            internalList = list(internalList)

        for idx, item in enumerate(internalList):
            if isinstance(item, Ref):
                internalList[idx] = self.getVar(item, context)
            elif isinstance(item, str) and item in _vars:
                internalList[idx] = _vars[item]
            elif isinstance(item, str) and item.startswith('<FunctionInstance '):
                func_name = item[len('<FunctionInstance '):-1]
                internalList[idx] = FUNCTIONS.get(func_name)
            elif isinstance(item, primitive):
                internalList[idx] = item
            elif isinstance(item, (list, tuple)):
                self._resolveList(item, scope, context)
            else:
                raise ValueError(f"Unsupported item type in list: {type(item)}")
        return internalList
    def _toInternalRepr(self, val: int | str | bool | list | tuple | dict):
        if isinstance(val, str) and val.startswith('<FunctionInstance '):
            func_name = val[len('<FunctionInstance '):-1]
            return FUNCTIONS.get(func_name)
        return val
    def _pushStackFrame(self, info: dict):
        if len(self.stack_frames) >= (Config.MAX_STACK_FRAMES or float('inf')):
            raise RuntimeError("Stackverflow: maximum stack frames exceeded")
        if Config.DEBUG_SCOPE:
            print(f"Pushing stack frame: {info['scope']}, {info['type']}, {info['context']}, {info.get('return_scope', None)}")
        self.stack_frames.append(info)
    def _requestTraceback(self, exc: Exception):
        print('\nTraceback, most recent call last: ')
        module_trace = []
        function_trace = []
        for i in list(reversed(self.stack_frames)):
            if i['type'] in ['function', 'broadcast']:
                _origin = _function_origination.get(i['scope'], '<self>')
                print(f"In {i['type']} \'{i['scope']}\'{f' (originating from {i['origination'].name})' if 'origination' in i else ''}, of module {_origin}")
                function_trace.append(i['scope'])
                module_trace.append(_origin)
        _line = getattr(exc, '_psLine', None) or (int(self.currentnode.line) if hasattr(self, 'currentnode') else -1)
        _col = getattr(exc, '_psCol', None) or (self.currentnode.col if self.currentnode else 0)
        if not module_trace:
            print('[The error was not detected in any function]')
            module_trace.append('<self>')
        if not function_trace:
            function_trace.append('<self>')
        print(f"""
Exception in [module {module_trace[-1]} at {function_trace[-1]}]:
[{type(exc).__name__}] {exc.__str__()}\n
Line: {_line}
Node: {self.currentnode}

Error Preview:
""")
        if module_trace[-1] == '<self>':
            _linetext_A = self.prog.evaluator._plaintext.splitlines()[_line-1]
            _linetext_B = self.prog.evaluator._plaintext.splitlines()[_line-1].lstrip()
            print(f"{_linetext_B if _line >= 0 else '[Could not retrieve preview of line]'}")
        else:
            with open(BASE_DIR / 'packages' / f"{module_trace[-1]}.txt", 'r') as f:
                lines = f.readlines()
                f.close()

            _linetext_A = lines[_line-1]
            _linetext_B = lines[_line-1].lstrip()
            print(f"{_linetext_B if _line >= 0 else '[Could not retrieve preview of line]'}")

        if self.currentnode:
            shifted = len(_linetext_A) - len(_linetext_B)
            adjusted = max(0, min(_col - shifted - 1, len(_linetext_B) - 1))
            print('~' * adjusted + '^' * self.currentnode._len  + '~' * (len(_linetext_B) - adjusted - self.currentnode._len))

        if Config.SHOW_PYTHON_TRACEBACK:
            traceback.print_exc()
    
    def evaluate(self, node, context: dict[str, object] = {}):
        if Config.DEBUG:
            global step_counter
            step_counter += 1
            print(f"Step {step_counter}: {node} (Scope: {self.scope})")

        self.currentnode = node
        if isinstance(node, Literal):
            return node.val
        elif isinstance(node, primitive):
            return node
        elif isinstance(node, Variable):
            return self.getVar(node.name, context, precedence='local' if self.scope != 'GLOBAL' else 'global')
        elif isinstance(node, CollectionLiteral):
            def popItem(name):
                if name == 'poplocal':
                    if self.scope == 'GLOBAL':
                        raise RuntimeError('PopLocal without a scope')
                    stack = LOCAL_STACK[-1]
                else:
                    stack = STACK
                return Natives._coerce(stack.pop())
            items = _CollectionReader(f'{node.prefix}{{{node.text}}}', popItem, node.line, node.col - 1).readDocument()
            return Natives._toIntIter(items)
        elif isinstance(node, DefineVar):
            if self.scope == 'GLOBAL':
                variables = GLOBAL_VAR
            else:
                variables = LOCAL_VAR_STACK[-1]
            if len(variables) >= ((Config.MAX_LOCAL_VARIABLES if self.scope != 'GLOBAL' else Config.MAX_VARIABLES) or float('inf')):
                raise RuntimeError(f"Maximum number of variables exceeded in the scope {self.scope}")

            if node.name in variables or node.name in FUNCTIONS:
                raise NameError(f"Variable '{node.name}' already exists")
            variables[node.name] = self.evaluate(node.value, context)
        elif isinstance(node, DefineGlobalVar):
            if node.name in GLOBAL_VAR or node.name in FUNCTIONS:
                raise NameError(f"Variable '{node.name}' already exists")

            if len(GLOBAL_VAR) >= (Config.MAX_VARIABLES or float('inf')):
                raise RuntimeError("Maximum number of global variables exceeded")
            GLOBAL_VAR[node.name] = self.evaluate(node.value, context)
        elif isinstance(node, SetVar):
            if Config.NO_VAR_MODIFY:
                raise RuntimeError("Variable modification is disabled by bootflag")

            value = self.evaluate(node.newValue, context)

            if self.scope != 'GLOBAL' and node.name in LOCAL_VAR_STACK[-1]:
                LOCAL_VAR_STACK[-1][node.name] = value
            elif node.name in GLOBAL_VAR:
                GLOBAL_VAR[node.name] = value
            else:
                raise NameError(f"Variable '{node.name}' is not defined")
        elif isinstance(node, SetGlobalVar):
            if Config.NO_VAR_MODIFY:
                raise RuntimeError("Variable modification is disabled by bootflag")
            value = self.evaluate(node.newValue, context)
            if node.name not in GLOBAL_VAR:
                raise NameError(f"Variable '{node.name}' is not defined")
            GLOBAL_VAR[node.name] = value
        elif isinstance(node, Push):
            if len(STACK) >= float(Config.MAX_STACK_DEPTH or 'inf'):
                raise RuntimeError("StackOverflow: maximum stack depth exceeded")
            val = self.evaluate(node.value, context)
            if isinstance(val, Function):
                val = f'<FunctionInstance {val.name}>'
            STACK.append(val)
        elif isinstance(node, PushLocal):
            if len(LOCAL_STACK[-1]) >= float(Config.MAX_LOCAL_STACK_DEPTH or 'inf'):
                raise RuntimeError("StackOverflow: maximum local stack depth exceeded")

            if self.scope == 'GLOBAL':
                raise RuntimeError('PushLocal without a scope')
            val = self.evaluate(node.value, context)
            if isinstance(val, Function):
                val = f'<FunctionInstance {val.name}>'
            LOCAL_STACK[-1].append(val)
        elif isinstance(node, Pop):
            if node.varOptional is not None:
                if node.varOptional in context:
                    context[node.varOptional] = self._toInternalRepr(STACK.pop())
                elif self.scope != 'GLOBAL' and node.varOptional in LOCAL_VAR_STACK[-1]:
                    LOCAL_VAR_STACK[-1][node.varOptional] = self._toInternalRepr(STACK.pop())
                elif node.varOptional in GLOBAL_VAR:
                    GLOBAL_VAR[node.varOptional] = self._toInternalRepr(STACK.pop())
                else:
                    raise NameError(f"Variable '{node.varOptional}' is not defined")
            else:
                return self._toInternalRepr(STACK.pop())
        elif isinstance(node, PopLocal):
            if self.scope == 'GLOBAL':
                raise RuntimeError('PopLocal without a scope')
            if node.varOptional is not None:
                if node.varOptional in context:
                    context[node.varOptional] = self._toInternalRepr(LOCAL_STACK[-1].pop())
                elif node.varOptional in LOCAL_VAR_STACK[-1]:
                    LOCAL_VAR_STACK[-1][node.varOptional] = self._toInternalRepr(LOCAL_STACK[-1].pop())
                elif node.varOptional in GLOBAL_VAR:
                    GLOBAL_VAR[node.varOptional] = self._toInternalRepr(LOCAL_STACK[-1].pop())
            else:
                return self._toInternalRepr(LOCAL_STACK[-1].pop())
        elif isinstance(node, (Apply, ApplyLocal)):
            if isinstance(node, ApplyLocal):
                raw_args = LOCAL_STACK[-1].pop()
            else:
                raw_args = STACK.pop()
            args = Natives._toPyIter(raw_args)

            if not isinstance(args, (list, tuple)):
                raise TypeError(
                    f"Apply expects a list or tuple of arguments, "
                    f"got {type(args).__name__}"
                )
            if node.func in FUNCTIONS:
                self.evaluate(Call(node.func, list(args)))
            elif node.func in NATIVES:
                is_local = isinstance(node, ApplyLocal)
                previous_local = Natives.local
                try:
                    Natives.local = is_local
                    for i in self._resolveList(args,'local' if is_local else 'global', context):
                        Natives._push(self.evaluate(i, context))
                    self.evaluate(InvokeNative(node.func, local=is_local))
                finally:
                    Natives.local = previous_local
            else:
                raise NameError(f'Function {node.func} is not defined')
        elif isinstance(node, Call):
            try:
                func = FUNCTIONS[node.func]
            except KeyError:
                if node._usedAsEvent:
                    if node.func in EVENTS:
                        func = EVENTS[node.func][self.stack_frames[-1]['index']].handler
                    else:
                        raise TypeError(f"Event listener '{node.func}' is not defined")
                elif isinstance(self.getVar(node.func, context), Function):
                    func = self.getVar(node.func, context)
                else:
                    raise TypeError(f"Function '{node.func}' is not defined")
            previous_scope = self.scope

            args_as_dict = dict(
                zip(func.args, (self.evaluate(arg, context) for arg in node.args))
            )
            for arg_name, default_value in func.defaults.items():
                if arg_name not in args_as_dict:
                    args_as_dict[arg_name] = self.evaluate(default_value, context)

            LOCAL_VAR_STACK.append({})
            LOCAL_STACK.append([])

            if node._usedAsEvent:
                self.scopes_stack.append(f'<EventInstance {node.func}>')
            else:
                self.scopes_stack.append(node.func)

            if len(args_as_dict) != len(func.args):
                raise TypeError(f"Function '{node.func}' takes {len(func.args)} argument{'' if len(func.args) == 1 else 's'} but {len(args_as_dict)} were given")

            active_functions = [
                frame['scope']
                for frame in self.stack_frames
                if frame['type'] in ('function', 'broadcast')
            ]

            if Config.NO_RECURSION and node.func in active_functions:
                raise RuntimeError(f"Recursion is disabled via bootflag, but function '{node.func}' was called recursively")
            if len(active_functions) >= Config.MAX_RECURSION_DEPTH:
                raise RuntimeError(
                    f"Maximum recursion depth ({Config.MAX_RECURSION_DEPTH}) exceeded"
                )
            if Config.PROFILE_FUNCTIONS:
                global FUNCTION_PROF_START
                FUNCTION_PROF_START = time.time()

            _info = {
                'type': 'function',
                "body": func.body,
                "index": 0,
                "context": args_as_dict,
                "scope": node.func,
                "return_scope": previous_scope,
            }
            if node.func != func.name:
                _info['origination'] = func
            self._pushStackFrame(_info)

            self.scope = node.func
        elif isinstance(node, InvokeNative):
            if Config.NO_NATIVES:
                raise RuntimeError('Native functions are disabled via boot configs')

            if node.name not in NATIVES:
                raise NameError(f'The native function, {node.name}, does not exist')
            if node.local and self.scope == 'GLOBAL':
                raise RuntimeError('CallLocalNative without a scope')

            Natives.local = node.local
            try:
                NATIVES[node.name](self)
            finally:
                Natives.local = False
        elif isinstance(node, BooleanExpression):
            left = self.evaluate(node.left, context)
            right = self.evaluate(node.right, context)

            if not isinstance(left, bool) or not isinstance(right, bool):
                raise TypeError("Boolean operands must be booleans")

            if node.op == "&&":
                return left and right
            elif node.op == "||":
                return left or right

            raise ValueError(f"Unknown boolean operator: {node.op}")
        elif isinstance(node, Condition):
            left = self.evaluate(node.left, context)
            right = self.evaluate(node.right, context)

            if node.op == "==":
                return left == right
            elif node.op == "!=":
                return left != right
            elif node.op== "<":
                return left < right
            elif node.op == ">":
                return left > right
            elif node.op == "<=":
                return left <= right
            elif node.op == ">=":
                return left >= right

            raise ValueError(f"Unknown comparison operator: {node.op}")
        elif isinstance(node, If):
            for condition, body in node.branches:
                if condition is True or self.evaluate(condition, context):
                    self._pushStackFrame({
                        'type': 'if',
                        'body': body,
                        'index': 0,
                        'context': context,
                        'scope': self.scope,
                    })
                    break
        elif isinstance(node, BlockExit):
            if not self.stack_frames:
                raise RuntimeError('Exit without a stackframe')
            self.finishFrame(node.depth)
        elif isinstance(node, Loop):
            if Config.NO_LOOP:
                raise RuntimeError('Loop statements are disabled via bootflag')

            self._pushStackFrame({
                'type': 'loop',
                'body': node.body,
                'index': 0,
                'context': context,
                'scope': self.scope,
            })
        elif isinstance(node, Return):
            isFunction = False
            for n, frame in enumerate(reversed(self.stack_frames[:-1])):
                if frame['type'] in ('function', 'broadcast'):
                    isFunction = True
                    returnDepth = n
                    break
            if not isFunction:
                raise RuntimeError('Return outside a function')

            try:
                local_stack = LOCAL_STACK[-returnDepth]
            except IndexError:
                raise RuntimeError('No function found to return to')
            local_stack.append(self.evaluate(node.value, context))
            LOCAL_STACK[-returnDepth] = local_stack

            if not self.evaluate(node.noExit, context):
                self.finishFrame(returnDepth+1)
        elif isinstance(node, Continue):
            isLoop = False
            for n, frame in enumerate(reversed(self.stack_frames)):
                if frame['type'] == 'loop':
                    isLoop = True
                    continueDepth = n + 1
                    break
            if not isLoop:
                raise RuntimeError('Continue outside a loop')
            frame = self.stack_frames[-continueDepth]
            frame['index'] = 0
        elif isinstance(node, Broadcast):
            self._pushStackFrame({
                'type': 'broadcast',
                'scope': f'<BroadcastInstance {node.message}>',
                'message': node.message,
                'queue': [h for h in EVENTS.get(node.message, [])],
                'context': context,
                'index': 0,
                'waiting': False
            })             
        elif isinstance(node, (DeleteVar, DeleteGlobalVar)):
            if isinstance(node, DeleteVar):
                variables = GLOBAL_VAR
            else:
                variables = LOCAL_VAR_STACK[-1]
            if node.name in variables:
                del variables[node.name]
            else:
                raise RuntimeError(f"Variable '{node.name}' not found for deletion")
    def advance(self):
        if self.pos + 1 >= len(self.nodes):
            return None

        self.pos += 1
        return self.nodes[self.pos]

    def finishFrame(self, depth: int = 1):
        if depth < 1:
            raise ValueError("Exit depth must be at least 1")

        for _ in range(depth):
            try:
                frame = self.stack_frames.pop()
            except IndexError:
                raise IndexError('No more execution frames left to finish')
            if frame['type'] == 'broadcast':
                if frame['index'] < len(frame['queue']):
                    raise RuntimeError('Cannot finish a broadcast frame if not all handlers of it have been executed')
                else:
                    continue
            if frame['type'] == 'function':
                return_scope = frame['return_scope']

                LOCAL_VAR_STACK.pop()
                LOCAL_STACK.pop()
                self.scopes_stack.pop()
                self.scope = return_scope
            if self.scope == 'GLOBAL':
                FUNCTION_PROF_END = time.time()
                if Config.PROFILE_FUNCTIONS and frame['type'] == 'function':
                    func_name = frame['scope']
                    elapsed_time = FUNCTION_PROF_END - FUNCTION_PROF_START
                    print(f"Function '{func_name}' and its related calls finished in {round(1000*elapsed_time, 2)}ms")

                    if Config.SUMMARIZE_FUNC_PROFILES:
                        _function_profiles.setdefault(func_name, (9999999, 0))
                        _function_profiles[func_name] = (min(_function_profiles[func_name][0], elapsed_time), max(_function_profiles[func_name][1], elapsed_time))

                if Config.SUMMARIZE_FUNC_PROFILES and not Config.PROFILE_FUNCTIONS:
                    print('Warning: the SUMMARIZE_FUNC_PROFILE flag depends on the PROFILE_FUNCTIONS flag')
            
    def step(self):
        if self.stack_frames:
            frame = self.stack_frames[-1]

            if frame['type'] == 'broadcast':
                if frame['waiting']:
                    return True

                if frame['index'] >= len(frame['queue']):
                    self.finishFrame()
                    return True

                handler = frame['queue'][frame['index']]
                if Config.LOG_EVENTS:
                    print(f"Executing handler {frame['index'] + 1} of {len(frame['queue'])} of event {frame['message']}")
                frame['waiting'] = True

                self.evaluate(handler(), frame["context"])
                frame['waiting'] = False
                frame['index'] += 1
                return True

            if frame["index"] >= len(frame["body"]):
                if frame['type'] == 'loop':
                    frame['index'] = 0
                else:
                    self.finishFrame()

                return True

            node = frame["body"][frame["index"]]
            self.currentnode = node
            frame["index"] += 1

            self.scope = frame["scope"]
            self.evaluate(node, frame["context"])
            return True
        global step_counter
        if step_counter >= (Config.MAX_INSTRUCTION_COUNT or float('inf')):
            raise RuntimeError("Maximum instruction step exceeded")
        
        if Config.STOP_AT_STEP is not None and step_counter >= Config.STOP_AT_STEP:
            raise StopIteration(f"Execution stopped at step {Config.STOP_AT_STEP}")

        step_counter += 1
        
        node = self.advance()

        if node is None:
            return False

        self.evaluate(node)
        return True
    def run(self):
        if Config.CLEAR_ON_LAUNCH:
            subprocess.run('cls', shell=True)
        while self.step():
            pass
        print(f'Finished program in {step_counter} steps')
    def rerun(self, go=True):
        global IMPORTED_FILES, IMPORTING_FILES, step_counter
        step_counter = 0
        STACK.clear()
        LOCAL_STACK.clear()
        LOCAL_VAR_STACK.clear()
        GLOBAL_VAR.clear()
        _function_profiles.clear()
        self.stack_frames.clear()
        self.scopes_stack.clear()
        self.scope = 'GLOBAL'
        self.pos = -1

        if Config.CLEAR_ON_LAUNCH:
            subprocess.run('cls', shell=True)

        if go:
            self.run()
    def reloadFile(self):
        EVENTS.clear()
        self.prog = Program(Evaluator(fileName))
        if self.nodes == self.prog.nodes:
            print('There was no change detected in the reload. Did you save?')
        self.nodes = self.prog.nodes
        self.rerun()

fileName = (Config.AUTO_FILE[1:-1] if Config.AUTO_FILE else input('Enter file name: '))
if Config.PRINT_ON_LAUNCH:
    print(Config.PRINT_ON_LAUNCH)
interpreter = Interpreter(Program(Evaluator(fileName)))

if Config.DUMP_AST:
    print('Dumping AST:')
    for node in interpreter.nodes:
        print(node)
if Config.DUMP_FUNCTIONS:
    print('Dumping functions:')
    for func in FUNCTIONS:
        print(func)
    if Config.DUMP_FULL_FUNCTIONS:
        print(FUNCTIONS[func].body, FUNCTIONS[func].args, FUNCTIONS[func].defaults)
if Config.DUMP_NATIVES:
    print('Dumping natives:')
    for native in NATIVES:
        print(native)

viewer = StackViewer(STACK, interpreter, GLOBAL_VAR, Config.EXECUTION_MS_LIMIT)
localsviewer = LocalsViewer(viewer, LOCAL_STACK, LOCAL_VAR_STACK, interpreter)
viewer._mainloop()
if Config.SUMMARIZE_FUNC_PROFILES:
    print('\nSummarizing all tracked functions...')
    headers = ["Function", "Min (ms)", "Max (ms)"]
    widths = [24, 13, 13]

    def row(values):
        return "  ".join(
            str(value).ljust(width)
            for value, width in zip(values, widths)
        )

    print(row(headers))
    print("-" * (sum(widths) + 6))

    for name, (minimum, maximum) in list(_function_profiles.items()):
        print(row([name,f"{1000*minimum:.2f}",f"{1000*maximum:.2f}"]))


