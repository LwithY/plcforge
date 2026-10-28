"""Per-request routing; an answer is not the completion of a coding task."""
from __future__ import annotations

import re

READ_TOOLS = {'list_files','read_file','search_code','plc_knowledge'}


def asks_model_identity(prompt: str) -> bool:
    text = re.sub(r'[\s?!？！，,。.!：:]', '', prompt).lower()
    return bool(re.fullmatch(
        r'(?:请问|请告诉我|告诉我)?(?:你(?:现在|当前)?(?:是(?:什么|哪个)(?:大语言|大)?模型|'
        r'(?:用的|使用的|用的是|使用的是)(?:什么|哪个)(?:大语言|大)?模型)|'
        r'你(?:的)?(?:底层模型|模型名称|模型名)(?:是(?:什么|哪个))?)|'
        r'(?:whatmodelareyou(?:using)?|whichmodelareyou(?:using)?|whatismodelname|whatisyourmodel(?:name)?)', text))


def request_mode(prompt: str, *, bound_task=False, pending=False, override='auto') -> str:
    if override not in {'auto','answer','code'}:
        raise ValueError('mode must be auto, answer or code')
    if override != 'auto':
        return override
    text = prompt.strip()
    if asks_model_identity(text):
        return 'answer'
    # Serialized course tasks are complete implementation requests.
    if bound_task and text.startswith('{'):
        return 'code'
    explaining = re.match(
        r'^(?:(?:请|请你|麻烦|帮我|只|仅|只需|只要|仅需)\s*)*'
        r'(?:解释|介绍|说明|分析|审查|阅读|查看|讲解|什么是|为什么|如何理解)|'
        r'^(?:please\s+)?(?:explain|describe|review|inspect|what\s+is|what\s+are|why\b|how\s+(?:do|does|can\s+I)\b)', text, re.I)
    mixed_action = re.search(r'(?:然后|并且|同时|再|并)\s*(?:帮我|请)?\s*(?:实现|编写|生成|修改|修复|创建|写)|'
                             r'\b(?:and|then)\s+(?:also\s+)?(?:write|implement|create|generate|modify|fix|repair)\b',text,re.I)
    if explaining and not mixed_action:
        return 'answer'
    direct_action = re.match(r'^(?:(?:请|请你|帮我|给我|先)\s*)*(?:实现|编写|生成|修改|修复|创建|写)',text)
    if re.search(r'解释|什么意思|什么含义|原理|生成.*(?:过程|流程)',text) and not direct_action and not mixed_action and not re.search(
            r'(?:请|帮我|给我|然后|并且|同时|再|并).{0,10}(?:实现|编写|生成|修改|修复|创建|写)',text):
        return 'answer'
    # Exclude a negated constraint, e.g. "不要修改测试，修复程序".
    positive = re.sub(r'(?:不要|不用|不需要|无需|不必|别)[^，,。;；\n]*',' ',text)
    action = re.search(r'实现|编写|写|生成|创建|新增|新建|修改|修复|补全|重构|开发|替换|优化|调整|添加|完成|做个|做一个|'
                       r'\b(?:write|implement|create|generate|modify|fix|repair|refactor|update|change|add|develop)\b',positive,re.I)
    subject = re.search(r'PLC|\bST\b|程序|代码|工程|功能块|控制|测试|文件|'
                        r'\b(?:code|program|function.?block|project|files?|tests?|motor|latch|timer|counter)\b',positive,re.I)
    if action and subject:
        return 'code'
    question = bool(re.search(r'[?？]|吗[。！!\s]*$|什么|为何|怎么|如何|\b(?:what|why|how|which|who)\b',text,re.I))
    if question or re.fullmatch(r'(?:你好|您好|嗨|谢谢|多谢|hello|hi|thanks)[!！。,.，\s]*',text,re.I):
        return 'answer'
    if bound_task or pending:
        return 'code'
    # Ambiguous short requests can clarify or choose tools. A successful write
    # upgrades this turn to code mode, so mutations never bypass validation.
    return 'auto'
