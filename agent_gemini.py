"""
agent_gemini.py - the fraud investigator agent, powered by Google Gemini.

It uses the SAME mcp_server.py as the Claude version, and runs the
tool-calling loop manually (more reliable than SDK auto-MCP, and it lets us
record every tool call AND its output for the UI later).

Setup (PowerShell, once per terminal):
    $env:GEMINI_API_KEY="your-key-here"

Run:
    python agent_gemini.py
    python agent_gemini.py "How are R0_1 and R1_2 connected?"
"""

import asyncio
import json
import os
import sys

from google import genai
from google.genai import types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Check Google AI Studio for current model names if this one is unavailable.
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
client = genai.Client()  # reads GEMINI_API_KEY from the environment

SYSTEM_PROMPT = """You are a fraud investigator working with a graph database of
accounts, devices, merchants and transactions.

You also have MEMORY and can ACT:
- First call get_lessons. After get_flagged_transactions, call recall_similar_cases
  with the flagged account ids and merchant ids, and use anything relevant.
- Before writing your final report: call freeze_account for each account you judge
  fraudulent (clear reason each time, skip ones get_frozen_accounts shows as frozen),
  call save_case ONCE (summary, verdict, ALL account and merchant ids involved),
  and call save_lesson for at most 2 short, general rules that helped.
- Then write the final report, and mention what you recalled from memory (if anything).

Work like an investigator:
- Start from flagged transactions, then follow connections with your tools.
- Use find_linked_accounts to discover rings, get_merchant_exposure to check
  where money goes, shortest_path to explain how accounts connect, and
  get_account_risk to score accounts.
- Base every claim on tool results. Never invent account or merchant ids.

Finish with a short report:
1. Verdict (which rings / accounts look fraudulent)
2. Evidence (the connections and numbers you found)
3. Recommended action (e.g. freeze, escalate, review)
Be concise and cite account ids."""

DEFAULT_QUESTION = (
    "Investigate all flagged transactions. Find which accounts are connected, "
    "whether separate cases are really one network, and what action to take."
)


async def generate_with_retry(contents, config, tries=6):
    """Call Gemini, waiting and retrying on temporary overload (503) or rate limit (429)."""
    from google.genai import errors

    fallbacks = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.6-flash"]
    models = [MODEL] + [m for m in fallbacks if m != MODEL]
    rounds = 4
    delay = 5
    for rnd in range(1, rounds + 1):
        for model in models:
            try:
                return await client.aio.models.generate_content(
                    model=model, contents=contents, config=config
                )
            except (errors.ServerError, errors.ClientError) as e:
                code = getattr(e, "code", None)
                if code not in (404, 429, 500, 503, 504):
                    raise
                print(f"   ({model}: error {code}, trying next model)")
        if rnd < rounds:
            print(f"   (all models busy, waiting {delay}s, round {rnd}/{rounds})")
            await asyncio.sleep(delay)
            delay *= 2
    raise RuntimeError("All Gemini models are busy right now. Try again in a few minutes.")


def _clean_schema(schema):
    """Remove JSON-schema keys Gemini does not accept (title, default, ...)."""
    if isinstance(schema, dict):
        return {
            k: _clean_schema(v)
            for k, v in schema.items()
            if k not in ("title", "default", "additionalProperties", "$schema")
        }
    if isinstance(schema, list):
        return [_clean_schema(v) for v in schema]
    return schema


def to_gemini_declaration(tool):
    """Convert one MCP tool into a Gemini function declaration."""
    schema = _clean_schema(tool.inputSchema or {"type": "object", "properties": {}})
    try:
        return types.FunctionDeclaration(
            name=tool.name,
            description=tool.description or "",
            parameters_json_schema=schema,
        )
    except Exception:
        # older SDK versions: use the plain 'parameters' field instead
        return types.FunctionDeclaration(
            name=tool.name,
            description=tool.description or "",
            parameters=schema,
        )


async def investigate(question: str, analyst: str = None, on_step=None):
    """Run one investigation. `on_step(step_dict)` is called after every tool call."""
    server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_server.py")
    server = StdioServerParameters(
        command=sys.executable,
        args=[server_path],
        # the MCP launcher does NOT pass most env vars on its own, so pass this one
        env={"ANALYST": analyst or os.getenv("ANALYST", "alice")},
    )

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1) ask the MCP server which tools exist, convert for Gemini
            listed = await session.list_tools()
            declarations = [to_gemini_declaration(t) for t in listed.tools]

            config = types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                tools=[types.Tool(function_declarations=declarations)],
                # we run the loop ourselves
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            )

            contents = [types.Content(role="user", parts=[types.Part(text=question)])]
            trace = []

            # 2) the agent loop
            for _ in range(15):  # safety limit
                response = await generate_with_retry(contents, config)

                calls = response.function_calls or []
                if not calls:
                    return response.text, trace

                # keep the model's turn in the history (needed for tool calling)
                contents.append(response.candidates[0].content)

                result_parts = []
                for fc in calls:
                    args = dict(fc.args or {})
                    print(f"-> calling {fc.name}({json.dumps(args)})")
                    result = await session.call_tool(fc.name, args)
                    output = "".join(
                        c.text for c in result.content if hasattr(c, "text")
                    )
                    trace.append({"tool": fc.name, "input": args, "output": output})
                    if on_step:
                        on_step(trace[-1])
                    result_parts.append(
                        types.Part.from_function_response(
                            name=fc.name, response={"result": output}
                        )
                    )
                contents.append(types.Content(role="user", parts=result_parts))

            return "Stopped: too many steps.", trace


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or DEFAULT_QUESTION
    print(f"Question: {question}\n")
    answer, trace = asyncio.run(investigate(question))
    print("\n" + "=" * 60)
    print(answer)
    print("=" * 60)
    print(f"\n({len(trace)} tool calls made)")
