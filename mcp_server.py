"""
mcp_server.py - exposes graph_tools.py as MCP tools.

An MCP server is a small program that tells an AI agent:
"here are the tools I offer, here is what each one does".
The agent then decides which tool to call.

IMPORTANT: this server talks over stdout, so never use print() in here.
"""

import json

from mcp.server.fastmcp import FastMCP

import graph_tools as gt
import memory_tools as mt

mcp = FastMCP("fraud-graph")


@mcp.tool()
def get_flagged_transactions() -> str:
    """List all transactions flagged as suspicious, with the account and merchant involved.
    Use this first to find cases worth investigating."""
    return json.dumps(gt.get_flagged_transactions())


@mcp.tool()
def get_account_profile(account_id: str) -> str:
    """Get basic facts about an account: name, age in days, devices used,
    number of transactions and total amount sent."""
    return json.dumps(gt.get_account_profile(account_id))


@mcp.tool()
def find_linked_accounts(account_id: str, max_hops: int = 2) -> str:
    """Find accounts connected to this account through shared devices.
    hops=1 means a directly shared device; hops=2 means a friend-of-a-friend, etc.
    Use this to discover fraud rings."""
    return json.dumps(gt.find_linked_accounts(account_id, max_hops))


@mcp.tool()
def shortest_path(account_a: str, account_b: str) -> str:
    """Find the shortest chain of connections (accounts and devices) between two accounts.
    Use this to explain HOW two accounts are related."""
    return json.dumps(gt.shortest_path(account_a, account_b))


@mcp.tool()
def get_merchant_exposure(merchant_id: str) -> str:
    """List which accounts paid a given merchant, how many times, and the total amount.
    Use this to check whether a merchant is a payout point for a ring."""
    return json.dumps(gt.get_merchant_exposure(merchant_id))


@mcp.tool()
def get_account_risk(account_id: str) -> str:
    """Compute an explainable risk score (0-100) for an account from graph signals:
    account age, number of linked accounts, and payment size. Returns the reasons."""
    return json.dumps(gt.get_account_risk(account_id))


# ------------------------------------------------------------- actions
@mcp.tool()
def freeze_account(account_id: str, reason: str) -> str:
    """ACTION: freeze an account you have judged fraudulent. Always give a clear reason
    based on evidence (shared devices, shell merchant payments, account age)."""
    return json.dumps(gt.freeze_account(account_id, reason))


@mcp.tool()
def get_frozen_accounts() -> str:
    """List accounts that are already frozen, so you don't freeze them twice."""
    return json.dumps(gt.get_frozen_accounts())


# -------------------------------------------------------------- memory
@mcp.tool()
def recall_similar_cases(entity_ids: list[str]) -> str:
    """MEMORY: look up past investigations that involved any of these account or
    merchant ids. Call this early, once you know which ids you are examining."""
    return json.dumps(mt.recall_similar_cases(entity_ids))


@mcp.tool()
def save_case(summary: str, verdict: str, entity_ids: list[str]) -> str:
    """MEMORY: save the finished investigation. entity_ids must list ALL account and
    merchant ids involved. Call once, before writing the final report."""
    return json.dumps(mt.save_case(summary, verdict, entity_ids))


@mcp.tool()
def get_lessons() -> str:
    """MEMORY: get general rules learned in earlier investigations. Call this first."""
    return json.dumps(mt.get_lessons())


@mcp.tool()
def save_lesson(rule: str) -> str:
    """MEMORY: remember a short, general rule that helped (not specific account ids).
    Example: 'Accounts under 30 days old that share devices with 3+ accounts are high risk.'"""
    return json.dumps(mt.save_lesson(rule))


if __name__ == "__main__":
    mcp.run()  # runs over stdio
