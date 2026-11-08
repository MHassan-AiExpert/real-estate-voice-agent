#!/usr/bin/env python3
"""Static checks on the generated workflow JSON:
 - unique node names, every connection endpoint exists
 - every non-sticky node is reachable from a trigger
 - every $('Node Name') reference inside expressions / Code nodes points at a real node
 - every Code node is syntactically valid JavaScript (node --check)
 - webhook paths are unique across workflows
"""
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
wf_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "workflows")
TRIGGERS = {"n8n-nodes-base.webhook", "n8n-nodes-base.scheduleTrigger", "n8n-nodes-base.errorTrigger"}
errors, warnings = [], []
paths = {}
REF = re.compile(r"\$\(\s*['\"]([^'\"]+)['\"]\s*\)")


def walk_strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from walk_strings(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk_strings(v)


files = sorted(glob.glob(os.path.join(wf_dir, "*.json")))
files = [f for f in files if "credentials.template" not in f]
total_nodes = 0
code_nodes = 0
for fp in files:
    wf = json.load(open(fp))
    name = wf["name"]
    nodes = {n["name"]: n for n in wf["nodes"]}
    total_nodes += len(nodes)
    if len(nodes) != len(wf["nodes"]):
        errors.append("%s: duplicate node names" % name)
    ids = [n["id"] for n in wf["nodes"]]
    if len(set(ids)) != len(ids):
        errors.append("%s: duplicate node ids" % name)
    # connections
    adj = {}
    for src, outs in wf["connections"].items():
        if src not in nodes:
            errors.append("%s: connection from unknown node %r" % (name, src))
            continue
        for branch in outs.get("main", []):
            for c in branch:
                if c["node"] not in nodes:
                    errors.append("%s: connection %r -> unknown node %r" % (name, src, c["node"]))
                adj.setdefault(src, set()).add(c["node"])
    # reachability
    start = [n for n, d in nodes.items() if d["type"] in TRIGGERS]
    if not start:
        errors.append("%s: no trigger node" % name)
    seen = set(start)
    stack = list(start)
    while stack:
        cur = stack.pop()
        for nx in adj.get(cur, ()):
            if nx not in seen:
                seen.add(nx)
                stack.append(nx)
    for n, d in nodes.items():
        if d["type"] == "n8n-nodes-base.stickyNote":
            continue
        if n not in seen:
            errors.append("%s: node %r is not reachable from a trigger" % (name, n))
    # references
    for n, d in nodes.items():
        for s in walk_strings(d["parameters"]):
            for ref in REF.findall(s):
                if ref not in nodes:
                    errors.append("%s: node %r references missing node %r" % (name, n, ref))
    # webhook paths
    for n, d in nodes.items():
        if d["type"] == "n8n-nodes-base.webhook":
            p = d["parameters"]["path"]
            if p in paths:
                errors.append("%s: webhook path %r also used in %s" % (name, p, paths[p]))
            paths[p] = name
    # code syntax
    for n, d in nodes.items():
        if d["type"] != "n8n-nodes-base.code":
            continue
        code_nodes += 1
        src = "(async function(){\n" + d["parameters"]["jsCode"] + "\n})"
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as t:
            t.write(src)
            tmp = t.name
        r = subprocess.run(["node", "--check", tmp], capture_output=True, text=True)
        os.unlink(tmp)
        if r.returncode != 0:
            errors.append("%s: Code node %r has a syntax error:\n%s" % (name, n, r.stderr.strip()[:600]))
    # HTTP nodes: expression sanity (balanced braces)
    for n, d in nodes.items():
        if d["type"] != "n8n-nodes-base.httpRequest":
            continue
        for k in ("url", "jsonBody"):
            v = d["parameters"].get(k)
            if isinstance(v, str) and v.startswith("={{") and v.count("{{") != v.count("}}"):
                errors.append("%s: HTTP node %r has unbalanced {{ }} in %s" % (name, n, k))

print("checked %d workflows, %d nodes (%d Code nodes), %d webhook paths" % (len(files), total_nodes, code_nodes, len(paths)))
for w in warnings:
    print("WARN ", w)
for e in errors:
    print("ERROR", e)
print("RESULT:", "FAIL (%d errors)" % len(errors) if errors else "OK")
sys.exit(1 if errors else 0)
