import re
import threading
from dataclasses import dataclass
from pathlib import Path

from stackview import StackViewer


class Node:
    pass

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
@dataclass
class Literal(Node):
    val: object
@dataclass
class InvokeNative(Node):
    name: str
    local: bool = False
@dataclass
class Variable(Node):
    name: str
@dataclass
class Condition(Node):
    left: object
    op: str
    right: object
@dataclass
class If(Node):
    branches: list

class Program:
    def __init__(self, nodes: list[type[Node]]):
        self.nodes = nodes
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
    @staticmethod
    def _toPyList(value: str):
        if isinstance(value, (list, tuple)):
            return value

        if value.startswith(('L{', 'T{')):
            content = value[2:-1]
            result = []
            depth = 0
            in_string = False
            start = 0
            for i, char in enumerate(content):
                if char == '"':
                    in_string = not in_string
                elif not in_string:
                    if char in '[{(':
                        depth += 1
                    elif char in ']})':
                        depth -= 1
                    elif char == ',' and depth == 0:
                        item = content[start:i].strip()
                        if item:
                            result.append(item)
                        start = i + 1
            item = content[start:].strip()
            if item:
                result.append(item)

            return result if value.startswith('L{') else tuple(result)

        raise ValueError(f"Invalid collection: {value!r}")
    @staticmethod
    def _toIntList(value: list | tuple):
        prefix = 'L' if isinstance(value, list) else 'T'
        return f"{prefix}{{{','.join(map(str, value))}}}"


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
    def pick(v):          # copy Nth item to top (N popped)
        n = Natives._pop(v)
        Natives._push(Natives._getStack()[-1 - n])

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
    def concat(v):
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
        Natives._push(list(Natives._pop(v)))
    @staticmethod
    def getelement(v):
        idx = Natives._pop(v)
        lst = Natives._toPyList(Natives._pop(v))
        element = lst[idx]

        parsed = Evaluator(str(element)).parseValue()
        if isinstance(parsed, Literal):
            if isinstance(parsed.val, str) and parsed.val[0:3] in ['T{', 'L{']:
                val = Natives._toPyList(parsed.val)
            else:
                val = parsed.val
        elif isinstance(parsed, Variable):
            val = v.getVar(parsed.name)
        Natives._push(val)
    @staticmethod
    def setelement(v):
        newVal = Natives._pop(v)
        idx = Natives._pop(v)
        lst = Natives._toPyList(Natives._pop(v))
        lst[idx] = newVal
        Natives._push(Natives._toIntList(lst))
    @staticmethod
    def iterappend(v):
        val = Natives._pop(v)
        lst = Natives._toPyList(Natives._pop(v))
        lst.append(val)
        Natives._push(Natives._toIntList(lst))
    @staticmethod
    def iterclear(v):
        lst = Natives._toPyList(Natives._pop(v))
        lst.clear()
        Natives._push(Natives._toIntList(lst))
    @staticmethod
    def delelement(v):
        idx = Natives._pop(v)
        lst = Natives._toPyList(Natives._pop(v))
        del lst[idx]

        Natives._push(Natives._toIntList(lst))
    @staticmethod
    def iterextend(v):
        extd = Natives._toPyList(Natives._pop(v))
        lst = Natives._toPyList(Natives._pop(v))
        lst.extend(extd)
        Natives._push(Natives._toIntList(lst))
    @staticmethod
    def sort(v):
        try:
            reverse = (Natives._pop(v) == 1)
        except IndexError:
            reverse = False
        lst = Natives._toPyList(Natives._pop(v))
        Natives._push(Natives._toIntList(sorted(lst, reverse=reverse)))

NATIVES = {
    name: getattr(Natives, name)
    for name in dir(Natives)
    if not name.startswith('_') and callable(getattr(Natives, name))
}

KEYWORDS = ['define', 'defineglobal', 'pop', 'push', 'function', 'end', 'call', 'set', 'setglobal', 'callnative', 'if', 'elif', 'else', 'poplocal', 'pushlocal', 'calllocalnative']
TOKEN_PATTERN = re.compile(
    r'"(?:\\.|[^"\\])*"'
    r'|\d+(?:\.\d+)?'
    r'|[A-Za-z_]\w*'
    r'|==|!=|<=|>='
    r'|[+\-*/%=<>]'
    r'[()\[\],]'
    r'|[ \t]+'
    r'|\n'
    r'|.'
)
BASE_DIR = Path(__file__).parent

FUNCTIONS: dict[str, Function] = {}

class Evaluator:
    def __init__(self, fileName: str):
        try:
            with open(BASE_DIR / f'{fileName}.txt', 'r') as f:
                self.source = f.read()
                f.close()
        except FileNotFoundError:
            self.source = fileName
        self.pos = 0
        self.tokens: list[tuple[str, str]] = []
        self.lex()
    
    def lex(self):
        tokens = []

        matches = iter(TOKEN_PATTERN.finditer(self.source))
        skip_until = 0

        for match in matches:
            if match.start() < skip_until:
                continue
            value = match.group()
            if value in ['[', '(']:
                depth = 1
                pos = match.end()

                while pos < len(self.source) and depth > 0:
                    if self.source[pos] == value:
                        depth += 1
                    elif self.source[pos] == (']' if value == '[' else ')'):
                        depth -= 1
                    pos += 1

                if depth != 0:
                    raise SyntaxError(f"Unclosed {'list' if value == '[' else 'tuple'}")

                tokens.append(("LIST" if value == '[' else 'TUPLE', self.source[match.end():pos - 1]))
                skip_until = pos
                continue
            elif value in (" ", "\t"):
                continue
            elif value == "\n":
                tokens.append(("NEWLINE", value))
            elif value.startswith('"'):
                tokens.append(("STRING", value[1:-1]))
            elif value.isdigit() or (value.count(".") == 1 and value.replace(".", "").isdigit()):
                number = float(value) if "." in value else int(value)
                tokens.append(("NUMBER", number))
            elif value in KEYWORDS:
                tokens.append(("KEYWORD", value))
            elif value[0].isalpha() or value[0] == "_":
                tokens.append(("IDENTIFIER", value))
            elif value in ['==', '>=', '<=', '!=', '>', '<']:
                tokens.append(("OPERATOR", value))
            elif value in "~&^|":
                tokens.append(("BINOP", value))
            elif value in "()[],":
                tokens.append(("PUNCTUATION", value))
            elif value.isspace():
                pass
            else:
                raise SyntaxError(f"Unexpected character: {value!r}")

        self.tokens = tokens
    
    def parseValue(self):
        tok = self.advance()

        if tok[0] == 'IDENTIFIER':
            return Variable(tok[1])
        elif tok[0] in ('NUMBER', 'STRING'):
            return Literal(tok[1])
        elif tok[0] in ('LIST', 'TUPLE'):
            return Literal(f'{tok[0][0]}{{{tok[1]}}}')

        raise SyntaxError(f"Expected a value, got {tok}")
    def parseCondition(self):
        left = self.parseValue()
        op = self.advance()

        if op is None or op[0] != 'OPERATOR':
            raise SyntaxError("Expected a comparison operator")

        if op[1] not in ('==', '!=', '<', '>', '<=', '>='):
            raise SyntaxError(f"Invalid comparison operator: {op[1]}")

        right = self.parseValue()

        if self.peek() and self.peek()[0] == 'NEWLINE':
            self.advance()

        return Condition(left, op[1], right)
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
        return If(branches)
    def parseStatement(self):
        tok = self.advance()
        if tok[0] == 'KEYWORD':
            keywordtok = tok[1]

            if keywordtok in ['define', 'defineglobal']:
                name = self.advance()[1]
                value = self.parseValue()
                if keywordtok == 'define':
                    return DefineVar(name, value)
                return DefineGlobalVar(name, value)
            elif keywordtok in ['pop', 'poplocal']:
                varOptional = None
                if self.peek() and self.peek()[0] == 'IDENTIFIER':
                    varOptional = self.advance()[1]
                if keywordtok == 'pop':
                    return Pop(varOptional)
                return PopLocal(varOptional)
            elif keywordtok in ['push', 'pushlocal']:
                value = self.parseValue()
                if keywordtok == 'push':
                    return Push(value)
                return PushLocal(value)
            elif keywordtok in ['set', 'setglobal']:
                name = self.advance()[1]
                value = self.parseValue()
                if keywordtok == 'set':
                    return SetVar(name, value)
                return SetGlobalVar(name, value)
            elif keywordtok == 'call':
                func = self.advance()[1]
                args = []

                while self.peek() is not None and self.peek()[0] != 'NEWLINE':
                    args.append(self.parseValue())

                return Call(func, args)
            elif keywordtok == 'function':
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
                func = Function(name, args, body)
                FUNCTIONS[name] = func
                return func
            elif keywordtok in ['callnative', 'calllocalnative']:
                name = self.advance()[1]

                return InvokeNative(name, (keywordtok == 'calllocalnative'))
            elif keywordtok == 'end':
                raise SyntaxError("'end' without a matching block")
            elif keywordtok == 'if':
                return self.parseIf()
            raise SyntaxError(f"Unknown keyword '{keywordtok}'")
        elif tok[0] in ["NUMBER", "STRING"]:
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
                self.advance()
                continue

            nodes.append(self.parseStatement())

        return nodes

GLOBAL_VAR: dict[str, object] = {}
STACK = []
LOCAL_VAR_STACK: list[dict[str, object]] = []
LOCAL_STACK: list[list] = []

primitive = (int, str, bool)
class Intepreter:
    def __init__(self, prog: Program):
        self.prog = prog
        self.nodes = prog.nodes
        self.pos = -1
        self.scope = 'GLOBAL'

    def getVar(self, name, context=None):
        if context is None:
            context = {}

        if self.scope != 'GLOBAL':
            if name in context:
                return context[name]

            if LOCAL_VAR_STACK and name in LOCAL_VAR_STACK[-1]:
                return LOCAL_VAR_STACK[-1][name]

        if name in GLOBAL_VAR:
            return GLOBAL_VAR[name]

        raise NameError(f"Variable '{name}' is not defined")
    def evaluate(self, node, context: dict[str, object] = {}):
        if isinstance(node, Literal):
            return node.val
        elif isinstance(node, primitive):
            return node
        elif isinstance(node, Variable):
            return self.getVar(node.name, context)
        elif isinstance(node, DefineVar):
            if self.scope == 'GLOBAL':
                variables = GLOBAL_VAR
            else:
                variables = LOCAL_VAR_STACK[-1]
            if node.name in variables:
                raise NameError(f"Variable '{node.name}' already exists")
            variables[node.name] = self.evaluate(node.value, context)
        elif isinstance(node, DefineGlobalVar):
            if node.name in GLOBAL_VAR:
                raise NameError(f"Variable '{node.name}' already exists")
            GLOBAL_VAR[node.name] = self.evaluate(node.value, context)
        elif isinstance(node, SetVar):
            value = self.evaluate(node.newValue, context)

            if self.scope != 'GLOBAL' and node.name in LOCAL_VAR_STACK[-1]:
                LOCAL_VAR_STACK[-1][node.name] = value
            elif node.name in GLOBAL_VAR:
                GLOBAL_VAR[node.name] = value
            else:
                raise NameError(f"Variable '{node.name}' is not defined")
        elif isinstance(node, SetGlobalVar):
            value = self.evaluate(node.newValue, context)
            if node.name not in GLOBAL_VAR:
                raise NameError(f"Variable '{node.name}' is not defined")
            GLOBAL_VAR[node.name] = value
        elif isinstance(node, Push):
            STACK.append(self.evaluate(node.value, context))
        elif isinstance(node, Pop):
            if node.varOptional is not None:
                if node.varOptional in context:
                    context[node.varOptional] = STACK.pop()
                elif self.scope != 'GLOBAL' and node.varOptional in LOCAL_VAR_STACK[-1]:
                    LOCAL_VAR_STACK[-1][node.varOptional] = STACK.pop()
                elif node.varOptional in GLOBAL_VAR:
                    GLOBAL_VAR[node.varOptional] = STACK.pop()
                else:
                    raise NameError(f"Variable '{node.varOptional}' is not defined")
            else:
                return STACK.pop()
        elif isinstance(node, PushLocal):
            if self.scope == 'GLOBAL':
                raise RuntimeError('PushLocal without a scope')
            LOCAL_STACK[-1].append(self.evaluate(node.value, context))
        elif isinstance(node, PopLocal):
            if self.scope == 'GLOBAL':
                raise RuntimeError('PopLocal without a scope')
            if node.varOptional is not None:
                if node.varOptional in context:
                    context[node.varOptional] = LOCAL_STACK[-1].pop()
                elif node.varOptional in GLOBAL_VAR:
                    GLOBAL_VAR[node.varOptional] = LOCAL_STACK[-1].pop()
                elif node.varOptional in LOCAL_VAR_STACK[-1]:
                    LOCAL_VAR_STACK[-1][node.varOptional] = LOCAL_STACK[-1].pop()
            else:
                return LOCAL_STACK[-1].pop()
        elif isinstance(node, Function):
            pass 
        elif isinstance(node, Call):
            func = FUNCTIONS[node.func]
            previous_scope = self.scope

            LOCAL_VAR_STACK.append({})
            LOCAL_STACK.append([])
            self.scope = node.func
            try:
                argsAsZip = dict(zip(func.args, (self.evaluate(arg, context) for arg in node.args)))
                for _node in func.body:
                    self.evaluate(_node, argsAsZip)
            finally:
                LOCAL_VAR_STACK.pop()
                LOCAL_STACK.pop()
                self.scope = previous_scope
        elif isinstance(node, InvokeNative):
            if node.name not in NATIVES:
                raise NameError(f'The native function, {node.name}, does not exist')
            if node.local and self.scope == 'GLOBAL':
                raise RuntimeError('CallLocalNative without a scope')

            Natives.local = node.local
            try:
                NATIVES[node.name](self)
            finally:
                Natives.local = False
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
                    for statement in body:
                        self.evaluate(statement, context)
                    break
    def advance(self):
        if self.pos + 1 >= len(self.nodes):
            return None

        self.pos += 1
        return self.nodes[self.pos]
    def step(self):
        node = self.advance()

        if node is None:
            return False

        self.evaluate(node)
        return True
    def run(self):
        while self.step():
            pass

    def rerun(self, go=True):
        STACK.clear()
        LOCAL_STACK.clear()
        LOCAL_VAR_STACK.clear()
        GLOBAL_VAR.clear()
        self.pos = -1
        if go:
            self.run()

intepreter = Intepreter(Program(Evaluator('source').parse()))
viewer = StackViewer(STACK, intepreter)
viewer.mainloop()
