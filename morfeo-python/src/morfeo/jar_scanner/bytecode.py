"""Iterador de instrucciones del atributo Code (JVMS, capítulo 6).

Solo decodifica longitudes y operandos; no simula la pila. Los operandos van en
``Instruction.operands`` con este significado:

- ``bipush``, ``sipush``: el valor con signo.
- ``ldc``, ``ldc_w``, ``ldc2_w``, accesos a campo, ``invoke*``, ``new``,
  ``anewarray``, ``checkcast``, ``instanceof``: el índice del constant pool
  (``invokeinterface`` añade el contador y ``multianewarray`` las dimensiones).
- cargas y almacenamientos con índice, ``ret``: el índice de la variable local.
- ``iinc``: índice y constante.
- saltos: el offset absoluto de destino.
- ``tableswitch``: ``(default, low, high, destino_0, …)`` con offsets absolutos.
- ``lookupswitch``: ``(default, npairs, clave_0, destino_0, …)``.
- ``newarray``: el código de tipo primitivo.

Las instrucciones con prefijo ``wide`` se devuelven con el opcode modificado y
``wide=True``.
"""

from __future__ import annotations

import struct
from typing import Iterator, NamedTuple

_NAMES = (
    "nop aconst_null iconst_m1 iconst_0 iconst_1 iconst_2 iconst_3 iconst_4 iconst_5 "
    "lconst_0 lconst_1 fconst_0 fconst_1 fconst_2 dconst_0 dconst_1 bipush sipush ldc ldc_w ldc2_w "
    "iload lload fload dload aload iload_0 iload_1 iload_2 iload_3 lload_0 lload_1 lload_2 lload_3 "
    "fload_0 fload_1 fload_2 fload_3 dload_0 dload_1 dload_2 dload_3 aload_0 aload_1 aload_2 aload_3 "
    "iaload laload faload daload aaload baload caload saload istore lstore fstore dstore astore "
    "istore_0 istore_1 istore_2 istore_3 lstore_0 lstore_1 lstore_2 lstore_3 fstore_0 fstore_1 "
    "fstore_2 fstore_3 dstore_0 dstore_1 dstore_2 dstore_3 astore_0 astore_1 astore_2 astore_3 "
    "iastore lastore fastore dastore aastore bastore castore sastore pop pop2 dup dup_x1 dup_x2 "
    "dup2 dup2_x1 dup2_x2 swap iadd ladd fadd dadd isub lsub fsub dsub imul lmul fmul dmul idiv "
    "ldiv fdiv ddiv irem lrem frem drem ineg lneg fneg dneg ishl lshl ishr lshr iushr lushr iand "
    "land ior lor ixor lxor iinc i2l i2f i2d l2i l2f l2d f2i f2l f2d d2i d2l d2f i2b i2c i2s lcmp "
    "fcmpl fcmpg dcmpl dcmpg ifeq ifne iflt ifge ifgt ifle if_icmpeq if_icmpne if_icmplt if_icmpge "
    "if_icmpgt if_icmple if_acmpeq if_acmpne goto jsr ret tableswitch lookupswitch ireturn lreturn "
    "freturn dreturn areturn return getstatic putstatic getfield putfield invokevirtual "
    "invokespecial invokestatic invokeinterface invokedynamic new newarray anewarray arraylength "
    "athrow checkcast instanceof monitorenter monitorexit wide multianewarray ifnull ifnonnull "
    "goto_w jsr_w breakpoint"
).split()

OPCODE_NAMES: dict[int, str] = dict(enumerate(_NAMES))
OPCODE_NAMES[0xFE] = "impdep1"
OPCODE_NAMES[0xFF] = "impdep2"
OPCODES: dict[str, int] = {name: op for op, name in OPCODE_NAMES.items()}

LDC, LDC_W, LDC2_W = OPCODES["ldc"], OPCODES["ldc_w"], OPCODES["ldc2_w"]
INVOKEVIRTUAL = OPCODES["invokevirtual"]
INVOKESPECIAL = OPCODES["invokespecial"]
INVOKESTATIC = OPCODES["invokestatic"]
INVOKEINTERFACE = OPCODES["invokeinterface"]
INVOKEDYNAMIC = OPCODES["invokedynamic"]
NEW = OPCODES["new"]
TABLESWITCH = OPCODES["tableswitch"]
LOOKUPSWITCH = OPCODES["lookupswitch"]
WIDE = OPCODES["wide"]
IINC = OPCODES["iinc"]

# Grupos por forma de operandos (el resto no tiene operandos)
_LOCAL_INDEX = {OPCODES[n] for n in ("iload lload fload dload aload istore lstore fstore dstore astore ret".split())}
_CP_U2 = {
    OPCODES[n]
    for n in (
        "ldc_w ldc2_w getstatic putstatic getfield putfield invokevirtual invokespecial invokestatic "
        "new anewarray checkcast instanceof"
    ).split()
}
_BRANCH16 = set(range(OPCODES["ifeq"], OPCODES["jsr"] + 1)) | {OPCODES["ifnull"], OPCODES["ifnonnull"]}
_BRANCH32 = {OPCODES["goto_w"], OPCODES["jsr_w"]}

_S1 = struct.Struct(">b")
_S2 = struct.Struct(">h")
_U2 = struct.Struct(">H")
_S4 = struct.Struct(">i")


class BytecodeError(ValueError):
    """Código de método mal formado o con un opcode desconocido."""


class Instruction(NamedTuple):
    offset: int
    opcode: int
    name: str
    length: int
    operands: tuple[int, ...] = ()
    wide: bool = False


def iter_instructions(code: bytes) -> Iterator[Instruction]:
    """Recorre las instrucciones de ``code`` en orden."""
    pos = 0
    end = len(code)
    try:
        while pos < end:
            ins = _decode(code, pos)
            yield ins
            pos += ins.length
    except (struct.error, IndexError) as exc:
        raise BytecodeError(f"código truncado en el offset {pos}") from exc


def _decode(code: bytes, pos: int) -> Instruction:
    op = code[pos]
    name = OPCODE_NAMES.get(op)
    if name is None:
        raise BytecodeError(f"opcode desconocido 0x{op:02x} en el offset {pos}")
    if op == OPCODES["bipush"]:
        return Instruction(pos, op, name, 2, (_S1.unpack_from(code, pos + 1)[0],))
    if op == OPCODES["sipush"]:
        return Instruction(pos, op, name, 3, (_S2.unpack_from(code, pos + 1)[0],))
    if op == LDC:
        return Instruction(pos, op, name, 2, (code[pos + 1],))
    if op in _LOCAL_INDEX:
        return Instruction(pos, op, name, 2, (code[pos + 1],))
    if op in _CP_U2:
        return Instruction(pos, op, name, 3, (_U2.unpack_from(code, pos + 1)[0],))
    if op == IINC:
        return Instruction(pos, op, name, 3, (code[pos + 1], _S1.unpack_from(code, pos + 2)[0]))
    if op in _BRANCH16:
        return Instruction(pos, op, name, 3, (pos + _S2.unpack_from(code, pos + 1)[0],))
    if op in _BRANCH32:
        return Instruction(pos, op, name, 5, (pos + _S4.unpack_from(code, pos + 1)[0],))
    if op == INVOKEINTERFACE:
        return Instruction(pos, op, name, 5, (_U2.unpack_from(code, pos + 1)[0], code[pos + 3]))
    if op == INVOKEDYNAMIC:
        return Instruction(pos, op, name, 5, (_U2.unpack_from(code, pos + 1)[0],))
    if op == OPCODES["newarray"]:
        return Instruction(pos, op, name, 2, (code[pos + 1],))
    if op == OPCODES["multianewarray"]:
        return Instruction(pos, op, name, 4, (_U2.unpack_from(code, pos + 1)[0], code[pos + 3]))
    if op == TABLESWITCH:
        return _tableswitch(code, pos)
    if op == LOOKUPSWITCH:
        return _lookupswitch(code, pos)
    if op == WIDE:
        return _wide(code, pos)
    return Instruction(pos, op, name, 1)


def _switch_start(pos: int) -> int:
    """Primer byte tras el relleno: el operando empieza en un múltiplo de 4 desde el inicio del código."""
    return pos + 1 + (-(pos + 1) % 4)


def _tableswitch(code: bytes, pos: int) -> Instruction:
    p = _switch_start(pos)
    default, low, high = (_S4.unpack_from(code, p + 4 * k)[0] for k in range(3))
    if high < low:
        raise BytecodeError(f"tableswitch con high < low en el offset {pos}")
    count = high - low + 1
    targets = [pos + _S4.unpack_from(code, p + 12 + 4 * k)[0] for k in range(count)]
    length = p + 12 + 4 * count - pos
    return Instruction(pos, TABLESWITCH, "tableswitch", length, (pos + default, low, high, *targets))


def _lookupswitch(code: bytes, pos: int) -> Instruction:
    p = _switch_start(pos)
    default, npairs = _S4.unpack_from(code, p)[0], _S4.unpack_from(code, p + 4)[0]
    if npairs < 0:
        raise BytecodeError(f"lookupswitch con npairs negativo en el offset {pos}")
    pairs: list[int] = []
    for k in range(npairs):
        key, target = _S4.unpack_from(code, p + 8 + 8 * k)[0], _S4.unpack_from(code, p + 12 + 8 * k)[0]
        pairs += (key, pos + target)
    length = p + 8 + 8 * npairs - pos
    return Instruction(pos, LOOKUPSWITCH, "lookupswitch", length, (pos + default, npairs, *pairs))


def _wide(code: bytes, pos: int) -> Instruction:
    op = code[pos + 1]
    name = OPCODE_NAMES.get(op, "")
    index = _U2.unpack_from(code, pos + 2)[0]
    if op == IINC:
        return Instruction(pos, op, name, 6, (index, _S2.unpack_from(code, pos + 4)[0]), wide=True)
    if op in _LOCAL_INDEX:
        return Instruction(pos, op, name, 4, (index,), wide=True)
    raise BytecodeError(f"wide aplicado a un opcode no válido 0x{op:02x} en el offset {pos}")
