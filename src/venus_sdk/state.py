"""Compatibilidade: o módulo foi para `venus_sdk.nodes.state`. Este caminho continua
importável (é o MESMO módulo) para não quebrar quem usa o SDK."""

import sys

from venus_sdk.nodes import state as _modulo

sys.modules[__name__] = _modulo
