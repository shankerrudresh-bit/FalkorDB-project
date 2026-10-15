"""
agent.py - a fraud investigator agent.

How it works (the "agent loop"):
  1. Start the MCP server and ask it which tools exist.
  2. Send your question + the tool list to Claude.
  3. If Claude wants a tool, we run it via MCP and send the result back.
  4. Repeat until Claude gives a final answer.

Setup (PowerShell, once per terminal):
    $env:ANTHROPIC_API_KEY="your-key-here"

Run:
    python agent.py
    python agent.py "How are R0_1 and R2_3 connected?"
"""

import asyncio
import json
import os
import sys

from anthropic import Anthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5")
client = Anthropic()  # reads ANTHROPIC_API_KEY from the environment

SYSTEM_PROMPT = """You are a fraud investigator working with a graph database of
accounts, devices, merchants and transactions.

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


async def investigate(question: str):
    server = StdioServerParameters(command=sys.executable, args=["mcp_server.py"])

    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # 1) ask the MCP server what tools it has
            listed = await session.list_tools()
            tools = [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": t.inputSchema,
                }
                for t in listed.tools
            ]

            messages = [{"role": "user", "content": question}]
            trace = []  # every tool call, for the UI later

            # 2) the agent loop
            for _ in range(15):  # safety limit
                response = client.messages.create(
                    model=MODEL,
                    max_tokens=2000,
                    system=SYSTEM_PROMPT,
                    tools=tools,
                    messages=messages,
                )
                messages.append({"role": "assistant", "content": response.content})

                if response.stop_reason != "tool_use":
                    answer = "".join(
                        b.text for b in response.content if b.type == "text"
                    )
                    return answer, trace

                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    print(f"-> calling {block.name}({json.dumps(block.input)})")
                    result = await session.call_tool(block.name, block.input)
                    output = "".join(
                        c.text for c in result.content if hasattr(c, "text")
                    )
                    trace.append(
                        {"tool": block.name, "input": block.input, "output": output}
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": output,
                        }
                    )
                messages.append({"role": "user", "content": tool_results})

            return "Stopped: too many steps.", trace


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or DEFAULT_QUESTION
    print(f"Question: {question}\n")
    answer, trace = asyncio.run(investigate(question))
    print("\n" + "=" * 60)
    print(answer)
    print("=" * 60)
    print(f"\n({len(trace)} tool calls made)")
