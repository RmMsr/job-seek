#!/usr/bin/env python3
"""Phase-0 prototype for per-job CV generation (spec:
docs/superpowers/specs/2026-09-03-per-job-cv-generation-design.md).

Throwaway. No schema / route / UI. The point is to eyeball, against real jobs
in the DB, whether:

  * the conservative draft is actually conservative and useful
  * `analyze_cv_gap` suggestions are worth acting on
  * `check_guardrails` findings are reliable
  * the composed-instruction model ("hard limits" + "tuning directives") steers
  * doc-write output looks right

The three prompts live *in this file* so they can be iterated without touching
app/. When they settle, they move into app/ai/tailor_cv.py.

Inputs are plain files under temp/ (created with sample content on first run):

    temp/cv_base.md          your real CV in markdown  -- REPLACE THE SAMPLE
    temp/cv_instruction.txt  the base instruction (one or two lines)
    temp/cv_guardrails.txt   hard limits, one per line
    temp/cv_directives.txt   per-job tuning directives, one per line (may be empty)

Usage:

    python scripts/cv_prototype.py --job-id 310
    python scripts/cv_prototype.py --job-id 310 --scope select,reorder,rephrase,summary
    python scripts/cv_prototype.py --job-id 310 --render

Needs the LLM endpoint reachable -- run with `dangerouslyDisableSandbox: true`.
Reads config.toml / job-seek.db from the cwd (copy them into the worktree first,
as the run-dev-server skill does).
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai.json_utils import extract_json
from app.config import load_config
from app.db import queries as q
from app.deps import _open_db, get_ai_client


# --------------------------------------------------------------------------- #
# Scope toggles -> instruction lines
# --------------------------------------------------------------------------- #

SCOPE_LINES = {
    "select": (
        "Include or omit existing bullets and whole sections based on their relevance "
        "to this job. Do not change any wording."
    ),
    "reorder": (
        "Reorder bullets and sections, and choose what leads each section, to foreground "
        "the experience this job values most."
    ),
    "rephrase": (
        "Reword existing bullets to mirror the job's terminology, WITHOUT introducing any "
        "skill, tool, employer, title, metric, or claim that is not already present in the "
        "base CV. Rephrasing may slightly upgrade scope or seniority of a claim."
    ),
    "adjust_opening": (
        "Create or rewrite a first introductary paragraph to connect with the job obbering organization or domain."
    ),
    "rewrite": (
        "Rewrite paragraphs freely to fill cv/job gaps or make the CV easier to consume."
    ),
}
CONSERVATIVE_SCOPE = ["select", "reorder"]


# --------------------------------------------------------------------------- #
# Prompt 1 -- conservative tailoring
# --------------------------------------------------------------------------- #

_TAILOR_SYSTEM = """You are an expert CV editor. You rewrite a candidate's base CV so a
busy recruiter sees, within ten seconds, why this person fits THIS job.

Your mandate -- be decisive:
- Lead every section with what this job values most. Push less relevant material down
  or out. A well-tailored CV looks materially different from the base: reordered,
  trimmed, re-emphasised, with a summary written around this specific role.
- A timid result that changes almost nothing is a FAILURE, even though it is "safe".
  Use the full latitude the "Permitted edits" give you.
- Cut hard. Length spent on irrelevant experience is length stolen from the match.

Your limits -- never cross them:
- Every skill, tool, employer, job title, date, degree, certification and metric in
  your output must be supported by the base CV. Reframe and re-emphasise freely;
  invent nothing.
- Obey every line under "Hard limits" exactly.
- Follow "Tuning directives" as far as their stated bounds allow, and no further.
- Stay within "Permitted edits": if an edit type is not listed, do not do it (e.g.
  with no "rephrase", reproduce bullet wording verbatim -- but you may still cut and
  reorder).

Output ONLY the tailored CV as raw markdown -- no preamble, no explanation, no code
fence around the whole document. Start directly with the CV content."""


def _strip_outer_fence(text: str) -> str:
    """Drop a leading <think>...</think> block (thinking mode) and peel exactly one
    ```markdown ... ``` wrapper if the model wrapped the whole document; leave inner
    fenced code blocks alone."""
    t = re.sub(r"^\s*<think>.*?</think>\s*", "", text, flags=re.DOTALL).strip()
    m = re.match(r"^```[a-zA-Z]*\n(.*)\n```$", t, re.DOTALL)
    return m.group(1).strip() if m else t


def _loads_json(raw: str | None, label: str):
    """Parse a model JSON reply, tolerating a trailing/leading prose or a
    truncated tail. Returns None (and prints the raw reply) on total failure so
    the run continues instead of crashing."""
    text = extract_json((raw or "").strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    if start != -1:
        for end in range(len(text), start, -1):
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                continue
    print(f"\n  [{label}] could not parse model reply as JSON; raw:\n{'  ' + (raw or '')[:1500]}")
    return None


def tailor_cv(client, model, base_cv: str, instruction: str, job_context: str,
              *, temperature: float = 0.5, think: bool = True) -> dict:
    user = f"# Instruction\n{instruction}\n\n# Base CV\n{base_cv}\n\n# Job\n{job_context}"
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _TAILOR_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        extra_body={"chat_template_kwargs": {"enable_thinking": think}},
    )
    md = _strip_outer_fence(resp.choices[0].message.content or "")
    if not md:
        raise RuntimeError("tailor_cv: model returned empty content")
    return {"markdown": md}


# --------------------------------------------------------------------------- #
# Prompt 2 -- gap analysis -> tuning-directive suggestions
# --------------------------------------------------------------------------- #

_GAP_SYSTEM = """You compare a candidate's tailored CV draft against a job posting and
propose concrete ways to steer the next revision. You do NOT rewrite the CV.

Each suggestion is a "tuning directive": a direction plus its bound -- what to
amplify or demote, and the floor/ceiling that keeps it honest and balanced.
Examples:
- "Foreground the Kafka and streaming experience the job leads with, but keep the
  data-modelling bullets present."
- "Compress the agency-era roles to one line each; do not drop the client count."
- "Lead the summary with the platform-engineering framing, without claiming an SRE
  title the base CV never uses."

Two categories:
- "strengthen": a requirement the job asks for that the draft underplays; a
  realistic closer match reachable by reframing existing experience; a genuine
  bridge from a current skill to something the job wants that is quick to adapt.
- "trim": detail that dilutes the match; signals of overqualification worth
  softening.

Only propose directives that existing CV content can honestly support. If the
draft is already well matched, return few or none.

Return exactly this JSON:
{"suggestions": [{"category": "strengthen"|"trim", "rationale": "<one line>",
"line": "<the tuning directive, phrased as direction + bound>"}]}"""


def analyze_cv_gap(client, model, base_cv: str, draft_md: str, job_context: str) -> dict:
    user = f"# Base CV\n{base_cv}\n\n# Current draft\n{draft_md}\n\n# Job\n{job_context}"
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _GAP_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    data = _loads_json(resp.choices[0].message.content, "gap") or {}
    out = []
    for s in data.get("suggestions", []):
        cat = s.get("category")
        if cat in ("strengthen", "trim") and s.get("line"):
            out.append({"category": cat, "rationale": s.get("rationale", ""), "line": s["line"]})
    return {"suggestions": out}


# --------------------------------------------------------------------------- #
# Prompt 3 -- guardrail compliance check
# --------------------------------------------------------------------------- #

_CHECK_SYSTEM = """You audit a tailored CV against a list of hard limits. For each
limit, decide whether the tailored CV respects it.

You are given the base CV too. A claim that is merely reworded from the base CV is
fine; a claim with no basis in the base CV, or one that upgrades scope/seniority
beyond it, is a violation.

verdict:
- "ok": the tailored CV clearly respects this limit
- "violated": the tailored CV clearly breaks it -- quote the offending text
- "unclear": you cannot tell

Return exactly this JSON:
{"findings": [{"guardrail": "<the limit, verbatim>", "verdict": "ok"|"violated"|"unclear",
"explanation": "<one line; quote offending CV text when violated>"}]}"""


def check_guardrails(client, model, guardrails: str, base_cv: str, tailored_md: str) -> dict:
    lines = [ln.strip() for ln in guardrails.splitlines() if ln.strip()]
    if not lines:
        return {"findings": []}
    numbered = "\n".join(f"{i+1}. {ln}" for i, ln in enumerate(lines))
    user = f"# Hard limits\n{numbered}\n\n# Base CV\n{base_cv}\n\n# Tailored CV\n{tailored_md}"
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _CHECK_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    data = _loads_json(resp.choices[0].message.content, "check") or {}
    return {"findings": data.get("findings", [])}


# --------------------------------------------------------------------------- #
# Composed instruction
# --------------------------------------------------------------------------- #

def compose_instruction(base_instruction: str, scope: list[str], guardrails: str, directives: str) -> str:
    parts = [base_instruction.strip(), "", "Permitted edits for this CV:"]
    for name in scope:
        parts.append(f"- {SCOPE_LINES[name]}")
    hard = [ln.strip() for ln in guardrails.splitlines() if ln.strip()]
    parts += ["", "Hard limits -- never violate:"]
    parts += [f"- {ln}" for ln in hard] or ["- (none)"]
    tun = [ln.strip() for ln in directives.splitlines() if ln.strip()]
    parts += ["", "Tuning directives -- bounded change trajectories:"]
    parts += [f"- {ln}" for ln in tun] or ["- (none)"]
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Change report -- deterministic, no LLM
# --------------------------------------------------------------------------- #

def _blocks(md: str) -> list[tuple[str, str, str]]:
    """Flatten markdown to (section_path, kind, text) for every bullet and
    non-empty paragraph line. Headings set the section path; they are not
    themselves blocks."""
    section: list[str] = []
    out: list[tuple[str, str, str]] = []
    in_comment = in_fence = False
    for raw in md.splitlines():
        line = raw.rstrip()
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.lstrip().startswith("<!--"):
            in_comment = "-->" not in line
            continue
        h = re.match(r"^(#{1,6})\s+(.*)", line)
        if h:
            level = len(h.group(1))
            section = section[: level - 1] + [h.group(2).strip()]
            continue
        s = line.strip()
        if not s or s == "---" or re.fullmatch(r"</?[a-z][^>]*>", s):
            continue
        path = " > ".join(section) or "(top)"
        if s[:2] in ("- ", "* "):
            out.append((path, "bullet", s[2:].strip()))
        else:
            out.append((path, "para", s))
    return out


def _norm(t: str, *, keep_fmt: bool = False) -> str:
    t = t.lower()
    if not keep_fmt:
        t = re.sub(r"[*_`#>]+", "", t)          # markdown emphasis / heading / quote marks
        t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)  # [label](url) -> label
    return re.sub(r"\s+", " ", t).strip()


def change_report(base_cv: str, draft: str) -> dict:
    base, new = _blocks(base_cv), _blocks(draft)
    free = list(range(len(new)))
    rows = []
    for bpath, _bkind, btext in base:
        best_i, best_r = None, 0.0
        for i in free:
            r = difflib.SequenceMatcher(None, _norm(btext), _norm(new[i][2])).ratio()
            if r > best_r:
                best_r, best_i = r, i
        if best_i is None or best_r < 0.6:
            rows.append({"kind": "dropped", "section": bpath, "text": btext})
            continue
        ntext = new[best_i][2]
        free.remove(best_i)
        if _norm(btext) == _norm(ntext):
            kind = "kept" if _norm(btext, keep_fmt=True) == _norm(ntext, keep_fmt=True) else "reformatted"
            rows.append({"kind": kind, "section": bpath, "text": btext})
        else:
            rows.append({"kind": "reworded", "section": bpath,
                         "before": btext, "after": ntext, "ratio": round(best_r, 2)})
    added = [{"kind": "added", "section": new[i][0], "text": new[i][2]} for i in free]

    def heads(blocks):
        return list(dict.fromkeys(p for p, _, _ in blocks))

    bh, nh = heads(base), heads(new)
    sm = difflib.SequenceMatcher(None, [_norm(h) for h in bh], [_norm(h) for h in nh])
    renamed, removed_h, added_h = [], [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "replace":
            for k in range(max(i2 - i1, j2 - j1)):
                b = bh[i1 + k] if i1 + k < i2 else None
                n = nh[j1 + k] if j1 + k < j2 else None
                if b and n:
                    renamed.append((b, n))
                elif b:
                    removed_h.append(b)
                elif n:
                    added_h.append(n)
        elif op == "delete":
            removed_h += bh[i1:i2]
        elif op == "insert":
            added_h += nh[j1:j2]
    return {"rows": rows, "added": added, "headings": {
        "renamed": renamed, "removed": removed_h, "added": added_h,
        "reordered": [_norm(h) for h in bh] != [_norm(h) for h in nh] and not renamed}}


def print_change_report(rep: dict) -> None:
    rows, added = rep["rows"], rep["added"]
    tally: dict[str, int] = {}
    for r in rows + added:
        tally[r["kind"]] = tally.get(r["kind"], 0) + 1
    order = ["kept", "reformatted", "reworded", "dropped", "added"]
    print("  bullets/lines:  " + "   ".join(f"{k}: {tally.get(k, 0)}" for k in order))

    h = rep["headings"]
    if len(h["renamed"]) > 4:
        print("\n  SECTION STRUCTURE: substantially restructured "
              f"({len(h['renamed'])} headings changed, {len(h['removed'])} removed, {len(h['added'])} added)"
              " -- see raw line-diff")
    elif h["renamed"] or h["removed"] or h["added"]:
        print("\n  SECTION STRUCTURE:")
        for b, n in h["renamed"]:
            print(f"    renamed:  {b}  ->  {n}")
        for x in h["removed"]:
            print(f"    removed:  {x}")
        for x in h["added"]:
            print(f"    added:    {x}")

    dropped = [r for r in rows if r["kind"] == "dropped"]
    if dropped:
        print("\n  DROPPED:")
        for r in dropped:
            print(f"    - [{r['section']}] {r['text']}")

    if added:
        print("\n  ADDED  (not traceable to a base line -- scrutinise):")
        for r in added:
            print(f"    + [{r['section']}] {r['text']}")

    reworded = [r for r in rows if r["kind"] == "reworded"]
    if reworded:
        print("\n  REWORDED:")
        for r in reworded:
            print(f"    [{r['section']}]  (similarity {r['ratio']})")
            print(f"      - {r['before']}")
            print(f"      + {r['after']}")

    reformatted = [r for r in rows if r["kind"] == "reformatted"]
    if reformatted:
        print(f"\n  REFORMATTED (text identical, markup only): {len(reformatted)}")


def write_html_diff(base_cv: str, draft: str, path: Path) -> None:
    html = difflib.HtmlDiff(wrapcolumn=80).make_file(
        base_cv.splitlines(), draft.splitlines(), "base CV", "tailored draft", context=True, numlines=2
    )
    path.write_text(html)


def job_context(job: dict) -> str:
    return textwrap.dedent(
        f"""\
        Title: {job.get('title', '')}
        Company: {job.get('company', '') or '(unknown)'}

        ## Summary
        {job.get('summary', '')}

        ## Full posting
        {job.get('simplified_content', '')[:9000]}"""
    )


# --------------------------------------------------------------------------- #
# Sample input files
# --------------------------------------------------------------------------- #

SAMPLE_CV = """\
# Alex Sample

Senior software engineer, 9 years, backend and data platforms. Oslo.

## Summary

Backend and data-platform engineer who has taken three services from prototype to
production. Comfortable owning a system end to end: schema, deploy, on-call.

## Experience

### Staff Engineer, Nordkraft Data (2021-present)
- Led the rebuild of the ingestion pipeline: 40 sources, ~2B events/day, on Kafka
  and Flink. Cut end-to-end latency from 6 min to 20 s.
- Introduced a schema registry and contract tests; onboarding a new source went
  from 2 weeks to 2 days.
- Mentored 4 engineers; ran the team's design-review process.

### Senior Backend Engineer, Blitz AS (2017-2021)
- Built the billing service (Python, Postgres) handling ~NOK 300M/year.
- Owned the migration from a monolith to 6 services; wrote the deployment tooling.
- Rotating on-call for the payments path.

### Backend Engineer, Konsulent Huset (2015-2017)
- Delivered REST backends for 5 client projects (Java, Spring).

## Skills

- Languages: Python, Java, Go (working), SQL
- Data: Kafka, Flink, Postgres, dbt, Airflow
- Infra: Kubernetes, Terraform, AWS, GitHub Actions

## Education

- MSc Computer Science, NTNU, 2015
"""

SAMPLE_INSTRUCTION = "Tailor the base CV to the job's requirements while staying strictly truthful.\n"

SAMPLE_GUARDRAILS = """\
Never state a job title the base CV does not use.
Never imply people-management of more than 4 engineers.
Keep the Education section exactly as written.
Never claim production experience with a tool not listed under Skills.
"""

SAMPLE_DIRECTIVES = ""  # populated by the user from gap-analysis suggestions


def ensure_inputs(paths: dict[str, Path]) -> bool:
    """Create any missing input file with sample content. Returns True if the
    base CV is still the untouched sample."""
    defaults = {
        "cv": SAMPLE_CV,
        "instruction": SAMPLE_INSTRUCTION,
        "guardrails": SAMPLE_GUARDRAILS,
        "directives": SAMPLE_DIRECTIVES,
    }
    for key, path in paths.items():
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(defaults[key])
            print(f"  created {path} (sample)")
    return paths["cv"].read_text().strip() == SAMPLE_CV.strip()


# --------------------------------------------------------------------------- #
# Rendering (best effort)
# --------------------------------------------------------------------------- #

def render(md_path: Path, css_path: Path | None, out_dir: Path) -> None:
    cli = shutil.which("doc-write-cli")
    if not cli:
        print(
            "\n[render] doc-write-cli not on PATH -- skipping.\n"
            "         pip install doc-write  (pulls WeasyPrint; needs pango/cairo/gdk-pixbuf)"
        )
        return
    pdf = out_dir / "cv_tailored.pdf"
    cmd = [cli, str(md_path), str(pdf)]
    if css_path and css_path.exists():
        cmd += ["--css", str(css_path)]
    print(f"\n[render] {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    if r.returncode == 0:
        print(f"[render] wrote {pdf}")
        png = out_dir / "cv_tailored.png"
        r2 = subprocess.run([cli, str(md_path), str(png)], capture_output=True, text=True)
        if r2.returncode == 0:
            print(f"[render] wrote {png} (+ per-page variants if multipage)")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def _hr(label: str) -> None:
    print(f"\n{'=' * 3} {label} {'=' * (72 - len(label))}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job-id", type=int, required=True)
    ap.add_argument("--scope", default="select,reorder",
                    help="comma list of: select,reorder,rephrase,summary (default: select,reorder)")
    ap.add_argument("--cv", type=Path, default=Path("temp/cv_base.md"))
    ap.add_argument("--instruction", type=Path, default=Path("temp/cv_instruction.txt"))
    ap.add_argument("--guardrails", type=Path, default=Path("temp/cv_guardrails.txt"))
    ap.add_argument("--directives", type=Path, default=Path("temp/cv_directives.txt"))
    ap.add_argument("--out", type=Path, default=Path("temp"))
    ap.add_argument("--css", type=Path, default=Path("temp/cv.css"))
    ap.add_argument("--render", action="store_true", help="also run doc-write-cli if present")
    ap.add_argument("--no-gap", action="store_true", help="skip gap analysis")
    ap.add_argument("--no-check", action="store_true", help="skip guardrail check")
    ap.add_argument("--temp", type=float, default=0.5, help="tailor sampling temperature (default 0.5)")
    ap.add_argument("--no-think", action="store_true", help="disable thinking mode for the tailor call")
    args = ap.parse_args()

    scope = [s.strip() for s in args.scope.split(",") if s.strip()]
    bad = [s for s in scope if s not in SCOPE_LINES]
    if bad:
        print(f"unknown scope: {bad}; valid: {list(SCOPE_LINES)}")
        return 2

    paths = {"cv": args.cv, "instruction": args.instruction,
             "guardrails": args.guardrails, "directives": args.directives}
    is_sample = ensure_inputs(paths)
    if is_sample:
        print("\n  NOTE: temp/cv_base.md is still the built-in SAMPLE CV. Output is only")
        print("        meaningful once you replace it with your real CV.\n")

    base_cv = args.cv.read_text()
    base_instruction = args.instruction.read_text()
    guardrails = args.guardrails.read_text()
    directives = args.directives.read_text()

    config = load_config()
    conn = _open_db(config)
    job = q.get_job(conn, args.job_id)
    if job is None:
        print(f"no job with id {args.job_id}")
        return 1

    client = get_ai_client()
    model = config.llm_model
    jc = job_context(job)
    instruction = compose_instruction(base_instruction, scope, guardrails, directives)

    _hr("JOB")
    print(f"#{job['id']}  {job['title']}")
    _hr("COMPOSED INSTRUCTION")
    print(instruction)

    _hr("CONSERVATIVE DRAFT")
    draft = tailor_cv(client, model, base_cv, instruction, jc,
                      temperature=args.temp, think=not args.no_think)["markdown"]
    print(draft)
    out_md = args.out / "cv_tailored.md"
    args.out.mkdir(parents=True, exist_ok=True)
    out_md.write_text(draft)
    print(f"\n(written to {out_md})")

    _hr("CHANGES FROM BASE")
    rep = change_report(base_cv, draft)
    print_change_report(rep)
    diff_html = args.out / "cv_diff.html"
    write_html_diff(base_cv, draft, diff_html)
    print(f"\n  raw line-diff: {diff_html}")

    if not args.no_gap:
        _hr("GAP ANALYSIS -> SUGGESTED TUNING DIRECTIVES")
        gap = analyze_cv_gap(client, model, base_cv, draft, jc)
        if not gap["suggestions"]:
            print("(none -- draft already well matched)")
        for i, s in enumerate(gap["suggestions"], 1):
            print(f"\n{i}. [{s['category']}] {s['rationale']}")
            print(f"   -> {s['line']}")

    if not args.no_check:
        _hr("GUARDRAIL COMPLIANCE CHECK")
        chk = check_guardrails(client, model, guardrails, base_cv, draft)
        if not chk["findings"]:
            print("(no hard limits configured)")
        for f in chk["findings"]:
            mark = {"ok": "  ok  ", "violated": " FAIL ", "unclear": "  ??  "}.get(f.get("verdict"), "  ?   ")
            print(f"[{mark}] {f.get('guardrail', '')}")
            if f.get("verdict") != "ok":
                print(f"          {f.get('explanation', '')}")

    if args.render:
        render(out_md, args.css, args.out)

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
