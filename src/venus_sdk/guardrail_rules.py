"""Compatibilidade: o módulo foi para `venus_sdk.prompts.guardrail_rules`. Este caminho continua
importável (é o MESMO módulo) para não quebrar quem usa o SDK."""

import sys

from venus_sdk.prompts import guardrail_rules as _modulo

sys.modules[__name__] = _modulo
