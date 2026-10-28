import html
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def tool_block(name, arguments, result, encode_times=2):
    """Bloc tool_calls tel qu'OpenWebUI le stocke (attributs échappés HTML)."""
    encoded = result
    for _ in range(encode_times):
        encoded = json.dumps(encoded, ensure_ascii=False)
    return (
        f'<details type="tool_calls" done="true" id="call_1" name="{name}" '
        f'arguments="{html.escape(json.dumps(arguments, ensure_ascii=False))}" '
        f'result="{html.escape(encoded)}" files="" embeds="">\n'
        "<summary>Tool Executed</summary>\n</details>\n"
    )


def pliage_tool_block(name, arguments, result):
    """Format observé sur Pliage : arguments encodés deux fois, résultat dans
    le corps du bloc (échappé HTML, JSON encodé deux fois), attribut result absent."""
    args = json.dumps(json.dumps(arguments))
    body = json.dumps(json.dumps(result, ensure_ascii=False), ensure_ascii=False)
    return (
        f'<details type="tool_calls" done="true" id="chatcmpl-tool-1" name="{name}" '
        f'arguments="{html.escape(args)}" files="" embeds="&quot;&quot;">\n'
        f"<summary>Tool Executed</summary>\n{html.escape(body)}\n</details>\n"
    )
