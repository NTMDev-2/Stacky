import re
import subprocess
import time
import typing
from dataclasses import dataclass, field
from pathlib import Path

from stackview import LocalsViewer, StackViewer


@dataclass
class Node:
    line: int = field(default=0, kw_only=True)

@dataclass
class DefineVar(Node):
    name: str
    value: object
@dataclass
class DefineGlobalVar(Node):
    name: str
    value: object
@dataclass
class SetVar(Node):
    name: str
    newValue: object
@dataclass
class SetGlobalVar(Node):
    name: str
    newValue: object

@dataclass
class Pop(Node):
    varOptional: str | None
@dataclass
class Push(Node):
    value: str | object
@dataclass
class PopLocal(Node):
    varOptional: str | None
@dataclass
class PushLocal(Node):
    value: str | object

@dataclass
class Function(Node):
    name: str
    args: list[str]
    body: list
@dataclass
class BlockEnd(Node):
    pass
@dataclass
class Call(Node):
    func: str 
    args: list
@dataclass
class InvokeNative(Node):
    name: str
    local: bool = False
@dataclass
class Apply(Node):
    func: str
@dataclass
class ApplyLocal(Node):
    func: str

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
@dataclass
class BlockExit(Node):
    depth: int = 1
@dataclass
class Loop(Node):
    body: list
@dataclass 
class Continue(Node):
    pass

@dataclass
class Import(Node):
    package: str
@dataclass
class Return(Node):
    value: object | None

class Program:
    def __init__(self, nodes: list[type[Node]]):
        self.nodes = nodes
class Ref(str):
    """A reference inside collections"""

class _CollectionReader:
    NUMBER = re.compile(r'-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?')
    IDENT = re.compile(r'[A-Za-z_]\w*')
    CLOSERS = {'[': ']', '(': ')', '{': '}'}
    KINDS = {'[': list, '(': tuple, '{': dict, 'L': list, 'T': tuple, 'M': dict}
    ESCAPES = {'n': '\n', 't': '\t', '\\': '\\', '"': '"'}

    def __init__(self, text: str, popHook=None):
        self.text = text
        self.pos = 0
        # Called with 'pop' or 'poplocal' when one appears as a bare item
        self.popHook = popHook
    def error(self, msg):
        return ValueError(f"{msg} at position {self.pos} in {self.text!r}")
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
                return self.popHook(name)
            return Ref(name)

        raise self.error(f'Unexpected character {ch!r}')
    def readString(self):
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
        raise self.error('Unterminated string')
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
        Natives._push(len(Natives._pop(v)))
    @staticmethod
    def substr(v):
        length = Natives._pop(v)
        start  = Natives._pop(v)
        s      = Natives._pop(v)
        Natives._push(s[start:start+length])

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
        condition = Natives._pop(v)
        message = Natives._pop(v)
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
    'apply', 'applylocal', 'return', 'continue'
]
TOKEN_PATTERN = re.compile(
    r'//[^\n]*'
    r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''
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

FUNCTIONS: dict[str, Function] = {}
IMPORTING_FILES: set[Path] = set()
IMPORTED_FILES: set[Path] = set()
            
class Evaluator:
    def __init__(self, fileName: str = '', forcePath: Path | None = None):
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
        self.pos = 0
        self.line = 1
        self.tokens: list[tuple[str, str]] = []
        self.lex()
    def lex(self):
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

        for match in matches:
            if match.start() < skip_until:
                continue
            value = match.group()
        
            if value in ['[', '(', '{']:
                depth = 1
                pos = match.end()

                if context in ('funcdef', 'ifdef'):
                    tokens.append(('PUNCTUATION', value))
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
                    tokens.append(('PUNCTUATION', value))
                    continue

                tokens.append((_type, self.source[match.end():pos - 1]))
                skip_until = pos
                continue
            elif value in (" ", "\t"):
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
                tokens.append(("NEWLINE", value))
            elif value.startswith(('"', "'")):
                tokens.append(("STRING", value[1:-1]))
            elif (value.isdigit() or value.startswith('-')) or (value.count(".") == 1 and value.replace(".", "").isdigit()):
                number = float(value) if "." in value else int(value)
                tokens.append(("NUMBER", number))
            elif value in KEYWORDS:
                if value == 'function':
                    context = 'funcdef'
                elif value == 'if':
                    context = 'ifdef'
                elif value in ['push', 'pop', 'pushlocal', 'poplocal', 'define', 'defineglobal', 'set', 'setglobal']:
                    context = 'valuedef'
                elif value == 'end':
                    context = ''
                tokens.append(("KEYWORD", value))
            elif value[0].isalpha() or value[0] == "_":
                tokens.append(("IDENTIFIER", value))
            elif value in ['==', '>=', '<=', '!=', '>', '<']:
                tokens.append(("OPERATOR", value))
            elif value in ['&&', '||']:
                tokens.append(("BOOLOP", value))
            elif value in "~&^|":
                tokens.append(("BINOP", value))
            elif value in "()[],":
                tokens.append(("PUNCTUATION", value))
            elif value.isspace():
                pass
            else:
                raise SyntaxError(f"Unexpected character: {value!r}")

        self.tokens = tokens

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
                left = BooleanExpression(left, operator, right, line=self.line)
            else:
                left = Condition(left, operator, right, line=self.line)

        return left
    def parseSingular(self):
        tok = self.advance()

        if tok[0] == 'KEYWORD' and tok[1] in ('pop', 'poplocal'):
            if tok[1] == 'pop':
                return Pop(None, line=self.line)
            return PopLocal(None, line=self.line)
        elif tok[0] == 'IDENTIFIER' and tok[1].lower() in ('true', 'false', 'null'):
            value = tok[1].lower()
            if value == 'true':
                return Literal(True, line=self.line)
            elif value == 'false':
                return Literal(False, line=self.line)
            return Literal(None, line=self.line)
        elif tok[0] == 'IDENTIFIER':
            return Variable(tok[1], line=self.line)
        elif tok[0] in ('NUMBER', 'STRING'):
            if tok[0] == 'STRING' and tok[1].startswith('<FunctionInstance '):
                raise RuntimeError("Illegal expression")
            if tok[0] == 'STRING' and len(str(tok[1])) > (Config.MAX_STRING_SIZE or float('inf')):
                raise RuntimeError("Maximum string length exceeded")
            if tok[0] == 'NUMBER' and Config.MAX_INTEGER is not None and abs(int(tok[1])) > Config.MAX_INTEGER:
                    raise RuntimeError("Maximum integer value exceeded") 
            return Literal(tok[1], line=self.line)
        elif tok[0] in ('LIST', 'TUPLE', 'MAP'):
            prefix = tok[0][0]
            found_pop = []
            def noteMarker(name):
                found_pop.append(name)
                return 0
            try:
                _CollectionReader(f'{prefix}{{{tok[1]}}}', noteMarker).readDocument()
            except ValueError as e:
                raise SyntaxError(
                    f"Invalid {tok[0].lower()} literal: {e}"
                ) from e
            if found_pop:
                return CollectionLiteral(prefix, tok[1], line=self.line)
            try:
                return Literal(
                    Natives._toIntIter(
                        Natives._toPyIter(f'{tok[0][0]}{{{tok[1]}}}')
                    ),
                    line=self.line
                )
            except ValueError as e:
                raise SyntaxError(
                    f"Invalid {tok[0].lower()} literal: {e}"
                ) from e
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
    def parseIf(self):
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

        if not self.peek() or self.peek() != ('KEYWORD', 'end'):
            raise SyntaxError("Expected 'end' to close if statement")

        self.advance()
        return If(branches, line=self.line)
    def parseStatement(self):
        tok = self.advance()
        if tok[0] == 'KEYWORD':
            keywordtok = tok[1]

            if keywordtok in ['define', 'defineglobal']:
                name = self.advance()[1]
                value = self.parseExpression()
                if keywordtok == 'define':
                    return DefineVar(name, value, line=self.line)
                return DefineGlobalVar(name, value, line=self.line)
            elif keywordtok in ['pop', 'poplocal']:
                varOptional = None
                if self.peek() and self.peek()[0] == 'IDENTIFIER':
                    varOptional = self.advance()[1]
                if keywordtok == 'pop':
                    return Pop(varOptional, line=self.line)
                return PopLocal(varOptional, line=self.line)
            elif keywordtok in ['push', 'pushlocal']:
                value = self.parseExpression()
                if keywordtok == 'push':
                    return Push(value, line=self.line)
                return PushLocal(value, line=self.line)
            elif keywordtok in ['set', 'setglobal']:
                name = self.advance()[1]
                value = self.parseExpression()
                if keywordtok == 'set':
                    return SetVar(name, value, line=self.line)
                return SetGlobalVar(name, value, line=self.line)
            elif keywordtok == 'call':
                func = self.advance()[1]
                args = []

                while self.peek() is not None and self.peek()[0] != 'NEWLINE':
                    args.append(self.parseExpression())

                return Call(func, args, line=self.line)
            elif keywordtok in ['apply', 'applylocal']:
                func = self.advance()[1]
                if keywordtok == 'apply':
                    return Apply(func, line=self.line)
                return ApplyLocal(func, line=self.line)
            elif keywordtok == 'function':
                if Config.NO_FUNCTIONS:
                    raise RuntimeError("Function definitions are disabled by bootflag")

                if len(FUNCTIONS) >= (Config.MAX_FUNCTIONS or float('inf')):
                    raise RuntimeError("Maximum number of functions exceeded")

                name = self.advance()[1]
                if self.advance()[1] != '(':
                    raise SyntaxError("Expected '(' after function name")

                args = []
                while self.peek() and self.peek()[1] != ')':
                    arg = self.advance()
                    if arg[0] == 'IDENTIFIER':
                        args.append(arg[1])
                    elif arg[1] != ',':
                        raise SyntaxError("Expected argument name or ','")

                if not self.peek():
                    raise SyntaxError("Expected ')' after function arguments")
                self.advance()

                body = self.parseBlock({'end'})

                if not self.peek():
                    raise SyntaxError(f"Function '{name}' is missing 'end'")

                self.advance()
                func = Function(name, args, body, line=self.line)
                FUNCTIONS[name] = func
                return func
            elif keywordtok in ['callnative', 'calllocalnative']:
                name = self.advance()[1]

                return InvokeNative(name, (keywordtok == 'calllocalnative'), line=self.line)
            elif keywordtok == 'end':
                raise SyntaxError("'end' without a matching block")
            elif keywordtok == 'if':
                return self.parseIf()
            elif keywordtok == 'exit':
                try:
                    depth = self.parseExpression()
                except SyntaxError:
                    depth = 1
                if isinstance(depth, Literal) and str(depth.val).isnumeric():
                    return BlockExit(depth.val, line=self.line)
                return BlockExit(line=self.line)
            elif keywordtok == 'loop':
                body = self.parseBlock({'end'})
                self.advance()
                return Loop(body, line=self.line)
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
                        imported_nodes = Evaluator(forcePath=path).parse()

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

                return Import(module[1], line=self.line)
            elif keywordtok == 'return':
                value = None
                if self.peek() is not None and self.peek()[0] != 'NEWLINE':
                    value = self.parseExpression()
                return Return(value, line=self.line)
            elif keywordtok == 'continue':
                return Continue(line=self.line)

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
        return tok
    def parse(self):
        self.pos = 0
        nodes = []

        while self.peek() is not None:
            if self.peek()[0] == 'NEWLINE':
                self.line += 1
                self.advance()
                continue

            nodes.append(self.parseStatement())

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

    NO_NATIVES: bool = False
    NO_FUNCTIONS: bool = False
    NO_IMPORTS: bool = False
    NO_RECURSION: bool = False
    NO_LOOP: bool = False
    NO_VAR_REDEFINE: bool = False

    PRINT_ON_LAUNCH: str = ''
    CLEAR_ON_LAUNCH: bool = False
    DUMP_AST: bool = False
    DUMP_FUNCTIONS: bool = False

    STOP_AT_STEP: int | None = None
    EXECUTION_MS_LIMIT: int = 5
    PROFILE_FUNCTIONS: bool = False

    AUTO_FILE: str = ''
    ALLOW_MANUAL_FLAG_ENTRY: bool = False

with open(BASE_DIR / 'bootflags.txt', 'r') as f:
    config_lines = f.readlines()

def parseKey(line: str):
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
print(f"All bootflags loaded in {round(1000*(_end_config-_start_config),2)}ms")
step_counter = 0

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

FUNCTION_PROF_START: float = 0
class Interpreter:
    def __init__(self, prog: Program):
        self.prog = prog
        self.running = False
        self.nodes = prog.nodes
        self.pos = -1
        self.scope = 'GLOBAL'
        self.scopes_stack: list[str] = []

        self.stack_frames: list[dict] = []

        self.currentnode = None

    def getVar(self, name, context=None, precedence='global'):
        if context is None:
            context = {}

        
        if precedence == 'local' and self.scope != 'GLOBAL':
            if name in context:
                return context[name]
            if LOCAL_VAR_STACK and name in LOCAL_VAR_STACK[-1]:
                return LOCAL_VAR_STACK[-1][name]

        if name in GLOBAL_VAR:
            return GLOBAL_VAR[name]
        elif name in FUNCTIONS:
            return FUNCTIONS[name]
        elif self.scope != 'GLOBAL':
            if name in context:
                return context[name]
            if LOCAL_VAR_STACK and name in LOCAL_VAR_STACK[-1]:
                return LOCAL_VAR_STACK[-1][name]

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
        self.stack_frames.append(info)
    
    def evaluate(self, node, context: dict[str, object] = {}):
        if Config.DEBUG:
            global step_counter
            step_counter += 1
            print(f"Step {step_counter}: {node} (Scope: {self.scope})")

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
            items = _CollectionReader(f'{node.prefix}{{{node.text}}}', popItem).readDocument()
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
            if Config.NO_VAR_REDEFINE:
                raise RuntimeError("Variable redefinition is disabled by bootflag")

            value = self.evaluate(node.newValue, context)

            if self.scope != 'GLOBAL' and node.name in LOCAL_VAR_STACK[-1]:
                LOCAL_VAR_STACK[-1][node.name] = value
            elif node.name in GLOBAL_VAR:
                GLOBAL_VAR[node.name] = value
            else:
                raise NameError(f"Variable '{node.name}' is not defined")
        elif isinstance(node, SetGlobalVar):
            if Config.NO_VAR_REDEFINE:
                raise RuntimeError("Variable redefinition is disabled by bootflag")
            value = self.evaluate(node.newValue, context)
            if node.name not in GLOBAL_VAR:
                raise NameError(f"Variable '{node.name}' is not defined")
            GLOBAL_VAR[node.name] = value
        elif isinstance(node, Push):
            if len(STACK) >= (Config.MAX_STACK_DEPTH or float('inf')):
                raise RuntimeError("Stackverflow: maximum stack depth exceeded")
            val = self.evaluate(node.value, context)
            if isinstance(val, Function):
                val = f'<FunctionInstance {val.name}>'
            STACK.append(val)
        elif isinstance(node, PushLocal):
            if len(LOCAL_STACK[-1]) >= (Config.MAX_LOCAL_STACK_DEPTH or float('inf')):
                raise RuntimeError("Stackverflow: maximum local stack depth exceeded")

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
        elif isinstance(node, (Function, Import)):
            return None
        elif isinstance(node, Call):
            try:
                func = FUNCTIONS[node.func]
            except KeyError:
                if isinstance(self.getVar(node.func, context), Function):
                    func = self.getVar(node.func, context)
                else:
                    raise TypeError(f"Function '{node.func}' is not defined")
            previous_scope = self.scope

            args_as_dict = dict(
                zip(func.args, (self.evaluate(arg, context) for arg in node.args))
            )

            LOCAL_VAR_STACK.append({})
            LOCAL_STACK.append([])
            self.scopes_stack.append(node.func)

            active_functions = [
                frame['scope']
                for frame in self.stack_frames
                if frame['type'] == 'function'
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

            self._pushStackFrame({
                'type': 'function',
                "body": func.body,
                "index": 0,
                "context": args_as_dict,
                "scope": node.func,
                "return_scope": previous_scope,
            })

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
            self.finish_frame(node.depth)
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
            for n, frame in enumerate(reversed(self.stack_frames)):
                if frame['type'] == 'function':
                    isFunction = True
                    returnDepth = n + 1
                    break
            if not isFunction:
                raise RuntimeError('Return outside a function')

            frame = self.stack_frames[-returnDepth]

            frame['return_value'] = (
                self.evaluate(node.value, context)
                if node.value is not None
                else None
            )
            frame['has_return_value'] = node.value is not None
            frame['index'] = len(frame['body'])
            self.finish_frame(returnDepth)
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
    def advance(self):
        if self.pos + 1 >= len(self.nodes):
            return None

        self.pos += 1
        return self.nodes[self.pos]

    def finish_frame(self, depth: int = 1):
        if depth < 1:
            raise ValueError("Exit depth must be at least 1")

        for _ in range(depth):
            if not self.stack_frames:
                return

            try:
                frame = self.stack_frames.pop()
            except IndexError:
                break

            if frame['type'] == 'function':
                return_value = frame.get('return_value')
                has_return_value = frame.get('has_return_value', False)
                return_scope = frame['return_scope']

                LOCAL_VAR_STACK.pop()
                LOCAL_STACK.pop()
                self.scopes_stack.pop()
                self.scope = return_scope

                if has_return_value:
                    if return_scope == 'GLOBAL':
                        STACK.append(return_value)
                    else:
                        LOCAL_STACK[-1].append(return_value)

            if self.scope == 'GLOBAL':
                FUNCTION_PROF_END = time.time()
                if Config.PROFILE_FUNCTIONS and frame['type'] == 'function':
                    func_name = frame['scope']
                    elapsed_time = FUNCTION_PROF_END - FUNCTION_PROF_START
                    print(f"Function '{func_name}' and its related calls finished in {round(1000*elapsed_time, 2)}ms")
    def step(self):
        if self.stack_frames:
            frame = self.stack_frames[-1]

            if frame["index"] >= len(frame["body"]):
                if frame['type'] == 'loop':
                    frame['index'] = 0
                else:
                    self.finish_frame()

                return True

            node = frame["body"][frame["index"]]
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
        self.stack_frames.clear()
        self.scopes_stack.clear()
        self.scope = 'GLOBAL'
        self.pos = -1

        if Config.CLEAR_ON_LAUNCH:
            subprocess.run('cls', shell=True)

        if go:
            self.run()
    def reloadFile(self):
        self.prog = Program(Evaluator(fileName).parse())
        if self.nodes == self.prog.nodes:
            print('There was no change detected in the reload. Did you save?')
        self.nodes = self.prog.nodes
        self.rerun()

fileName = (Config.AUTO_FILE[1:-1] if Config.AUTO_FILE else input('Enter file name: '))
if Config.PRINT_ON_LAUNCH:
    print(Config.PRINT_ON_LAUNCH)
interpreter = Interpreter(Program(Evaluator(fileName).parse()))

if Config.DUMP_AST:
    print('Dumping AST:')
    for node in interpreter.nodes:
        print(node)
if Config.DUMP_FUNCTIONS:
    print('Dumping functions:')
    for func in FUNCTIONS:
        print(func)

viewer = StackViewer(STACK, interpreter, GLOBAL_VAR, Config.EXECUTION_MS_LIMIT)
localsviewer = LocalsViewer(viewer, LOCAL_STACK, LOCAL_VAR_STACK, interpreter)
viewer._mainloop()
