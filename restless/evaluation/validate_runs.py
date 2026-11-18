"""Validate merged multi-host run artifacts without silently overwriting conflicts."""
from __future__ import annotations
import argparse,hashlib,json,sys
from collections import defaultdict
from pathlib import Path

def run_records(results):
    """Stage 0 work records are intermediate generation runs, not experiment runs."""
    return [p for p in sorted(Path(results).rglob("run_record.json")) if "_stage0" not in p.parts]
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def fairness_violations(results):
    """Effective config of every instance, read back from the records rather than the CLI."""
    from restless.pipeline.protocol import effective_fairness
    groups=defaultdict(dict)
    for path in run_records(results):
        try:record=json.loads(path.read_text())
        except json.JSONDecodeError:continue
        config=record.get("config")
        if not config or not record.get("complete"):continue
        meta=record.get("meta",{});groups[meta.get("instance_id")][record.get("method")]=config
    found=[]
    for key,records in sorted(groups.items(),key=str):
        if "respstl" not in records or len(records)<2:continue
        result=effective_fairness(records)
        if not result["passed"]:found.append({"instance":key,"illegal":result["illegal_differences"]})
    return found
def main():
    p=argparse.ArgumentParser();p.add_argument("--results",default="results");p.add_argument("--expected",default="",help="Optional text/CSV whose first column is an expected run_id")
    a=p.parse_args();by_id=defaultdict(list);invalid=[]
    for path in run_records(a.results):
        try:record=json.loads(path.read_text())
        except json.JSONDecodeError as exc:invalid.append({"path":str(path),"error":str(exc)});continue
        run_id=record.get("run_id")
        if not run_id:invalid.append({"path":str(path),"error":"missing run_id"});continue
        by_id[str(run_id)].append({"path":str(path),"hash":digest(path),"complete":bool(record.get("complete"))})
    duplicates={key:rows for key,rows in by_id.items() if len(rows)>1};conflicts={key:rows for key,rows in duplicates.items() if len({r["hash"] for r in rows})>1}
    missing=[]
    if a.expected:
        lines=Path(a.expected).read_text().splitlines();expected={line.split(",",1)[0].strip() for line in lines if line.strip()}
        expected.discard("run_id");missing=sorted(expected-set(by_id))
    incomplete=sorted(key for key,rows in by_id.items() if not any(r["complete"] for r in rows))
    report={"records":sum(map(len,by_id.values())),"unique_run_ids":len(by_id),"identical_duplicates":{k:v for k,v in duplicates.items() if k not in conflicts},"conflicts":conflicts,"invalid":invalid,"incomplete":incomplete,"missing":missing,"fairness_violations":fairness_violations(a.results),"passed":not conflicts and not invalid and not missing}
    report["passed"]=report["passed"] and not report["fairness_violations"]
    print(json.dumps(report,indent=2));sys.exit(0 if report["passed"] else 1)
if __name__=="__main__":main()
