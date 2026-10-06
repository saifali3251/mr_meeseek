"""Browser-facing onboarding surfaces:

  /ops/onboard          — a team's wizard (create a request, review/edit the
                          drafted manifest, run a trial build, submit it)
  /ops/admin/onboarding — the platform-review queue (approve -> publish, or
                          reject with a reason)

Same posture as ops_console.py's /ops/* routes: in-process calls into
OnboardingService, no shared HOLODECK_TOKEN in the page. /ops/onboard requires
a resolved team (docs/AUTOMATIC_ONBOARDING.md's "team token/link"); the admin
queue doesn't scope by team — reaching it at all is the only gate, same
loopback-trust posture the rest of /ops already relies on.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from holodeck.onboarding.models import (DESTRUCTIVE_FIELDS, MANIFEST_FIELDS,
                                        ManifestField, OnboardingRequest, RepoSpec)
from holodeck.onboarding.service import (OnboardingConflict, OnboardingNotFound,
                                         OnboardingService)
from holodeck.teams import Team, TeamStore, resolve_team, set_team_cookies


class RepoSpecIn(BaseModel):
    name: str
    url: str
    branch: str = "main"
    role: str = "app"
    test_cmd: Optional[str] = None
    depends_on: list[str] = Field(default_factory=list)
    via: Optional[str] = None
    env_var: Optional[str] = None


class NewRequestBody(BaseModel):
    app_name: str
    contact: str = ""
    team_slug: Optional[str] = None
    jira_project: Optional[str] = None
    test_cmd: Optional[str] = None
    preview_port: Optional[str] = None
    repos: list[RepoSpecIn]


class ValidateRepoBody(BaseModel):
    url: str
    branch: str = "main"


class FieldEdits(BaseModel):
    fields: dict[str, Optional[str]]


class RejectBody(BaseModel):
    reason: str


def _req_dict(req: OnboardingRequest) -> dict:
    d = asdict(req)
    d["status"] = req.status.value
    d["destructive_unreviewed"] = req.has_unreviewed_destructive_fields()
    return d


def build_onboarding_pages_router(svc: OnboardingService, teams: TeamStore) -> APIRouter:
    r = APIRouter(tags=["onboarding-ui"])

    def _team_or_403(request: Request, response: Response) -> Team:
        team = resolve_team(request, teams)
        if team is None:
            raise HTTPException(403, "no team scope — open this page via your team's share link "
                                     "(create one from the aside on /ops)")
        set_team_cookies(response, team)
        return team

    def _owned_or_404(request_id: str, team: Team) -> OnboardingRequest:
        try:
            req = svc.get(request_id)
        except OnboardingNotFound:
            raise HTTPException(404, "no such onboarding request")
        if req.team_slug != team.slug:
            raise HTTPException(403, "not your team's request")
        return req

    @r.post("/ops/onboard/validate-repo")
    def onboard_validate_repo(body: ValidateRepoBody) -> dict:
        from holodeck.onboarding.git_validator import validate_git_repo
        return validate_git_repo(body.url, body.branch)

    @r.get("/ops/onboard", response_class=HTMLResponse, include_in_schema=False)
    def onboard_page() -> str:
        return _WIZARD_PAGE

    @r.get("/ops/onboard/state")
    def onboard_state(request: Request, response: Response) -> dict:
        team = _team_or_403(request, response)
        return {"team": {"slug": team.slug, "name": team.name},
               "manifest_fields": list(MANIFEST_FIELDS),
               "destructive_fields": list(DESTRUCTIVE_FIELDS),
               "requests": [_req_dict(x) for x in svc.list_for_team(team.slug)]}

    @r.post("/ops/onboard/requests")
    def onboard_create(body: NewRequestBody, request: Request, response: Response) -> dict:
        team = resolve_team(request, teams)
        target_slug = (team.slug if team else (body.team_slug or "core")).strip().lower()
        if team is None:
            team = teams.get(target_slug)
            if team is None:
                try:
                    team = teams.create(target_slug.replace("-", " ").title(), contact=body.contact or "", slug=target_slug)
                except Exception:
                    team = None
        if team is None:
            team = _team_or_403(request, response)
        else:
            set_team_cookies(response, team)
        try:
            req = svc.create(target_slug, body.app_name, body.contact,
                             [RepoSpec(**x.model_dump()) for x in body.repos],
                             jira_project=body.jira_project,
                             test_cmd=body.test_cmd,
                             preview_port=body.preview_port)
            if body.test_cmd:
                req.manifest["HOLO_TEST_CMD"] = ManifestField(value=body.test_cmd, source="user_input", confidence="high")
            if body.preview_port:
                req.manifest["HOLO_APP_PORT"] = ManifestField(value=str(body.preview_port), source="user_input", confidence="high")
            if body.test_cmd or body.preview_port:
                svc.store.put(req)
        except OnboardingConflict as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))
        return _req_dict(req)

    @r.patch("/ops/onboard/requests/{request_id}/fields")
    def onboard_edit(request_id: str, body: FieldEdits, request: Request, response: Response) -> dict:
        team = _team_or_403(request, response)
        _owned_or_404(request_id, team)
        try:
            return _req_dict(svc.update_fields(request_id, body.fields))
        except OnboardingConflict as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @r.post("/ops/onboard/requests/{request_id}/trial")
    def onboard_trial(request_id: str, request: Request, response: Response) -> dict:
        team = _team_or_403(request, response)
        _owned_or_404(request_id, team)
        try:
            return _req_dict(svc.run_trial(request_id))
        except OnboardingConflict as e:
            raise HTTPException(409, str(e))

    @r.post("/ops/onboard/requests/{request_id}/submit")
    def onboard_submit(request_id: str, request: Request, response: Response) -> dict:
        team = _team_or_403(request, response)
        _owned_or_404(request_id, team)
        try:
            return _req_dict(svc.submit(request_id))
        except OnboardingConflict as e:
            raise HTTPException(409, str(e))

    # ---- platform review — the last human gate before a manifest is real ----

    @r.get("/ops/admin/onboarding", response_class=HTMLResponse, include_in_schema=False)
    def admin_page() -> str:
        return _ADMIN_PAGE

    @r.get("/ops/admin/onboarding/state")
    def admin_state() -> dict:
        return {"manifest_fields": list(MANIFEST_FIELDS),
               "destructive_fields": list(DESTRUCTIVE_FIELDS),
               "requests": [_req_dict(x) for x in svc.list_pending_approval()]}

    @r.post("/ops/admin/onboarding/requests/{request_id}/approve")
    def admin_approve(request_id: str) -> dict:
        try:
            return _req_dict(svc.approve(request_id))
        except OnboardingNotFound:
            raise HTTPException(404, "no such onboarding request")
        except OnboardingConflict as e:
            raise HTTPException(409, str(e))

    @r.post("/ops/admin/onboarding/requests/{request_id}/reject")
    def admin_reject(request_id: str, body: RejectBody) -> dict:
        try:
            return _req_dict(svc.reject(request_id, body.reason))
        except OnboardingNotFound:
            raise HTTPException(404, "no such onboarding request")
        except OnboardingConflict as e:
            raise HTTPException(409, str(e))

    return r


# --- shared page chrome: the same token names as ops_console.py's _PAGE, kept
# to just what these two pages actually use (no templating engine exists to
# share the full block from — see ops_console.py's own module docstring). ---
_TOKENS = r"""
:root{
  --paper:#0d1015;--surface:#161b22;--surface-2:#1c222c;--ink:#e7edf3;--ink-2:#adb7c2;--ink-3:#73808e;
  --line:#232b35;--line-2:#313a46;--green:#4bc99a;--green-deep:#83e2bb;--green-wash:#102a20;--green-line:#2f5f49;
  --warn:#e6b84e;--warn-wash:#2c2411;--crit:#f0857a;--crit-wash:#2c1613;--grey:#73808e;--grey-wash:#1c222c;
  --mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;--sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
}
:root[data-theme="light"]{
  --paper:#faf9f6;--surface:#fff;--surface-2:#f7f6f1;--ink:#1c1b18;--ink-2:#57544d;--ink-3:#86827a;
  --line:#e6e3db;--line-2:#d5d1c7;--green:#1a7a5b;--green-deep:#0f5a40;--green-wash:#e6f4ef;--green-line:#b6ddcd;
  --warn:#a6741a;--warn-wash:#f6eeda;--crit:#b23b32;--crit-wash:#f8e9e7;--grey:#86827a;--grey-wash:#efede7;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.5 var(--sans);-webkit-font-smoothing:antialiased}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
header{border-bottom:1px solid var(--line);padding:16px 24px;display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;background:var(--surface)}
.brand{display:flex;align-items:center;gap:10px;font-weight:750}
.brand .mk{width:22px;height:22px;border-radius:6px;background:var(--green);color:#06231b;display:grid;place-items:center;font-size:13px}
.brand small{display:block;font:600 10px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--ink-3);margin-top:2px}
.hmeta{font-size:12px;color:var(--ink-3);display:flex;gap:14px;align-items:center;flex-wrap:wrap}
main{padding:20px 24px 60px;max-width:920px;margin:0 auto}
.card{border:1px solid var(--line);border-radius:11px;background:var(--surface);margin-bottom:18px;overflow:hidden}
.card-h{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:13px 16px;border-bottom:1px solid var(--line)}
.card-h h2{margin:0;font-size:.95rem;font-weight:700}
.card-b{padding:16px}
.h{font:600 10px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--ink-3);margin:0 0 10px}
select,input,textarea{font:inherit;color:var(--ink);background:var(--paper);border:1px solid var(--line-2);border-radius:8px;padding:8px 10px;width:100%}
select:focus,input:focus,textarea:focus{outline:2px solid var(--green);outline-offset:1px;border-color:var(--green)}
label{font-size:11.5px;color:var(--ink-3);display:block;margin-bottom:4px}
.field{margin-bottom:10px}
.btn{font:inherit;font-weight:650;border-radius:8px;padding:9px 14px;cursor:pointer;border:1px solid var(--green);background:var(--green);color:#06231b}
.btn:hover{background:var(--green-deep);border-color:var(--green-deep)}
.btn.ghost{background:transparent;color:var(--ink-2);border-color:var(--line-2);font-weight:600;padding:6px 12px;font-size:12.5px}
.btn.ghost:hover{background:var(--surface-2);color:var(--ink)}
.btn.danger{border-color:var(--crit);color:var(--crit);background:transparent}
.btn:disabled{opacity:.45;cursor:not-allowed}
table{border-collapse:collapse;width:100%;font-size:13px}
thead th{text-align:left;font:600 10px var(--mono);letter-spacing:.07em;text-transform:uppercase;color:var(--ink-3);padding:9px 16px;background:var(--surface-2);border-bottom:1px solid var(--line)}
tbody td{padding:11px 16px;border-bottom:1px solid var(--line);vertical-align:middle}
tbody tr:last-child td{border-bottom:none}
tbody tr{cursor:pointer} tbody tr:hover td{background:var(--surface-2)}
tbody tr.sel td{background:var(--green-wash)}
.pill{display:inline-flex;align-items:center;gap:6px;font-size:11px;font-weight:650;padding:3px 9px;border-radius:999px;white-space:nowrap;border:1px solid transparent}
.pill .d{width:7px;height:7px;border-radius:50%}
.pill.ready{background:var(--green-wash);color:var(--green-deep);border-color:var(--green-line)}
.pill.prov{background:var(--warn-wash);color:var(--warn)}
.pill.failed{background:var(--crit-wash);color:var(--crit)}
.pill.released{background:var(--grey-wash);color:var(--grey)}
@media(prefers-color-scheme:dark){.pill.ready{color:var(--green)}}
.repo-row{display:grid;grid-template-columns:1.2fr 1.8fr .8fr 1fr;gap:8px;margin-bottom:8px;align-items:end}
@media(max-width:720px){.repo-row{grid-template-columns:1fr}}
.field-row{display:grid;grid-template-columns:220px 1fr 90px;gap:8px;align-items:start;padding:8px 0;border-bottom:1px solid var(--line)}
.field-row:last-child{border-bottom:none}
.field-row .name{font:12px var(--mono);color:var(--ink-2);padding-top:9px}
.badge{font-size:10.5px;padding:2px 7px;border-radius:999px;white-space:nowrap}
.badge.needs{background:var(--warn-wash);color:var(--warn)}
.badge.inferred{background:var(--green-wash);color:var(--green-deep)}
.badge.edit{background:var(--grey-wash);color:var(--grey)}
.warn-box{background:var(--warn-wash);border:1px solid var(--warn);border-radius:8px;padding:10px 12px;font-size:12.5px;color:var(--ink);margin-bottom:12px}
pre.log{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:12px;font:12px var(--mono);max-height:280px;overflow:auto;white-space:pre-wrap}
.empty{padding:32px;text-align:center;color:var(--ink-3)}
.toast{position:fixed;bottom:22px;left:50%;transform:translateX(-50%) translateY(20px);background:var(--surface);border:1px solid var(--line);color:var(--ink);padding:10px 16px;border-radius:9px;font-size:13px;opacity:0;pointer-events:none;transition:.2s}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}.toast.bad{border-color:var(--crit)}
.acts{display:flex;gap:6px;flex-wrap:wrap}
"""

_JS_COMMON = r"""
const $=s=>document.querySelector(s), esc=s=>(s==null?"":String(s)).replace(/[&<>"]/g,c=>({"&":"&lt;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function toast(m,bad){const t=$("#toast");t.textContent=m;t.className="toast show"+(bad?" bad":"");clearTimeout(window._tt);window._tt=setTimeout(()=>t.className="toast",2600);}
const PILL={draft:["prov","draft"],trial_running:["prov","trial running"],trial_passed:["ready","trial passed"],
  trial_failed:["failed","trial failed"],pending_approval:["prov","awaiting review"],
  rejected:["failed","rejected"],published:["ready","published"]};
function pill(s){const[c,l]=PILL[s]||["released",s];return `<span class="pill ${c}"><span class="d"></span>${esc(l)}</span>`;}
function fieldBadge(f){if(!f)return `<span class="badge needs">no signal</span>`;
  if(f.source==="team_edit")return `<span class="badge edit">you edited this</span>`;
  if(f.source==="needs_review")return `<span class="badge needs">needs your input</span>`;
  return `<span class="badge inferred">${esc(f.source.replace("inferred:","inferred from "))}</span>`;}
"""

_WIZARD_PAGE = r"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark light"><title>Holodeck — Add your app</title>
<style>""" + _TOKENS + r"""</style></head><body>
<header>
  <div class="brand"><span class="mk">◇</span><span>Holodeck<small>Add your app</small></span></div>
  <div class="hmeta"><a class="btn ghost" href="/ops">← back to console</a></div>
</header>
<main>
  <section class="card">
    <div class="card-h"><h2>Your apps</h2><span class="sub" id="teamName" style="color:var(--ink-3);font-size:12px"></span></div>
    <div class="card-b"><table>
      <thead><tr><th>App</th><th>Status</th><th style="text-align:right">Actions</th></tr></thead>
      <tbody id="reqRows"></tbody>
    </table></div>
  </section>

  <section class="card" id="detailCard" style="display:none">
    <div class="card-h"><h2 id="detailTitle">Draft</h2>
      <div class="acts" id="detailActs"></div>
    </div>
    <div class="card-b" id="detailBody"></div>
  </section>

  <section class="card">
    <div class="card-h"><h2>New app</h2></div>
    <div class="card-b">
      <div class="field"><label>App name (this becomes the manifest key)</label>
        <input id="newApp" placeholder="e.g. my-service" autocomplete="off"></div>
      <div class="field"><label>Contact (email or Slack)</label>
        <input id="newContact" placeholder="engineer@meeseek.io" autocomplete="off"></div>
      <p class="h" style="margin-top:16px">Repos (add one row per repo — more than one means a
        composite/dependency graph; "depends on" + "via" only matter when there's more than one)</p>
      <div id="repoRows"></div>
      <button class="btn ghost" type="button" onclick="addRepoRow()" style="margin-bottom:12px">+ add repo</button>
      <div><button class="btn" onclick="createRequest()">Draft manifest</button></div>
    </div>
  </section>
</main>
<div class="toast" id="toast"></div>
<script>""" + _JS_COMMON + r"""
let state={requests:[],manifest_fields:[],destructive_fields:[]}, selected=null, repoN=0;

function repoRowHtml(i){return `<div class="repo-row" data-i="${i}">
  <div><label>Repo name</label><input data-f="name" placeholder="backend"></div>
  <div><label>Git URL (or a local path for testing)</label><input data-f="url" placeholder="https://github.com/example/…"></div>
  <div><label>Role</label><select data-f="role"><option>app</option><option>gateway</option><option>db-owner</option><option>frontend</option><option>worker</option></select></div>
  <div><label>Depends on (names, comma-sep)</label><input data-f="depends_on" placeholder="backend"></div>
</div>`;}
function addRepoRow(){$("#repoRows").insertAdjacentHTML("beforeend",repoRowHtml(repoN++));}
addRepoRow();

function collectRepos(){return [...document.querySelectorAll(".repo-row")].map(row=>{
  const get=f=>row.querySelector(`[data-f="${f}"]`).value.trim();
  const name=get("name"),url=get("url");
  if(!name||!url)return null;
  return {name,url,role:get("role")||"app",
    depends_on:get("depends_on")?get("depends_on").split(",").map(s=>s.trim()).filter(Boolean):[]};
}).filter(Boolean);}

async function createRequest(){
  const app_name=$("#newApp").value.trim(), contact=$("#newContact").value.trim(), repos=collectRepos();
  if(!app_name){toast("Enter an app name",true);return;}
  if(!repos.length){toast("Add at least one repo",true);return;}
  try{const res=await fetch("/ops/onboard/requests",{method:"POST",headers:{"content-type":"application/json"},
      body:JSON.stringify({app_name,contact,repos})});
    if(!res.ok)throw new Error((await res.json()).detail||res.status);
    const req=await res.json();
    toast(`Drafted a manifest for ${app_name}`);$("#newApp").value="";
    await refresh();select(req.request_id);
  }catch(e){toast("Draft failed: "+e.message,true);}}

function reqRows(){const tb=$("#reqRows");
  if(!state.requests.length){tb.innerHTML=`<tr><td colspan="3"><div class="empty">No apps yet — draft one below.</div></td></tr>`;return;}
  tb.innerHTML=state.requests.map(r=>`<tr class="${selected===r.request_id?"sel":""}" onclick="select('${esc(r.request_id)}')">
    <td><b>${esc(r.app_name)}</b></td><td>${pill(r.status)}</td>
    <td style="text-align:right"><span style="font-size:11.5px;color:var(--ink-3)">click to open</span></td></tr>`).join("");}

function fieldRows(req,editable){return state.manifest_fields.map(name=>{
  const f=req.manifest[name], destructive=state.destructive_fields.includes(name);
  const val=f&&f.value!=null?f.value:"";
  return `<div class="field-row">
    <div class="name">${esc(name)}${destructive?' <span title="runs a real command against the database">⚠</span>':""}</div>
    <div>${editable
      ?`<input data-field="${esc(name)}" value="${esc(val)}" placeholder="${destructive?'needs your review — never auto-trusted':'blank'}">`
      :`<span class="mono" style="font-size:12.5px">${esc(val)||'<span class=\"dim\">blank</span>'}</span>`}</div>
    <div>${fieldBadge(f)}</div>
  </div>`;}).join("");}

function detailActions(req){
  const acts=[];
  if(req.status==="draft"||req.status==="trial_failed"){
    acts.push(`<button class="btn ghost" onclick="saveFields('${req.request_id}')">Save edits</button>`);
    acts.push(`<button class="btn" ${req.destructive_unreviewed?"disabled title=\"review HOLO_MIGRATE_CMD/HOLO_SEED_CMD first\"":""} onclick="runTrial('${req.request_id}')">Run trial build</button>`);
  }
  if(req.status==="trial_passed")
    acts.push(`<button class="btn" onclick="submitForReview('${req.request_id}')">Submit for platform review</button>`);
  return acts.join("");}

function renderDetail(){const card=$("#detailCard");
  const req=state.requests.find(r=>r.request_id===selected);
  if(!req){card.style.display="none";return;}
  card.style.display="";
  $("#detailTitle").textContent=`${req.app_name} · `+({draft:"draft",trial_running:"trial running…",
    trial_passed:"trial passed",trial_failed:"trial failed",pending_approval:"awaiting platform review",
    rejected:"rejected",published:"published"}[req.status]||req.status);
  $("#detailActs").innerHTML=detailActions(req);
  const editable=req.status==="draft"||req.status==="trial_failed";
  const destructiveNote=req.destructive_unreviewed
    ?`<div class="warn-box">HOLO_MIGRATE_CMD / HOLO_SEED_CMD run a real command against a real database —
       recon never trusts a guess for these. Fill them in (or confirm they're correct) before a trial build can run.</div>`
    :"";
  const rejectNote=req.reject_reason?`<div class="warn-box">Platform review sent this back: ${esc(req.reject_reason)}</div>`:"";
  const log=req.trial_log?`<p class="h" style="margin-top:14px">Trial build log</p><pre class="log">${esc(req.trial_log)}</pre>`:"";
  const err=req.trial_error?`<p style="color:var(--crit);font-size:12.5px">${esc(req.trial_error)}</p>`:"";
  $("#detailBody").innerHTML=`${rejectNote}${destructiveNote}${fieldRows(req,editable)}${err}${log}`;}

async function saveFields(id){
  const fields={};document.querySelectorAll("#detailBody [data-field]").forEach(el=>{fields[el.dataset.field]=el.value||null;});
  try{const res=await fetch(`/ops/onboard/requests/${encodeURIComponent(id)}/fields`,{method:"PATCH",
      headers:{"content-type":"application/json"},body:JSON.stringify({fields})});
    if(!res.ok)throw new Error((await res.json()).detail||res.status);
    toast("Saved");await refresh();select(id);
  }catch(e){toast("Save failed: "+e.message,true);}}

async function runTrial(id){toast("Running trial build…");
  try{const res=await fetch(`/ops/onboard/requests/${encodeURIComponent(id)}/trial`,{method:"POST"});
    if(!res.ok)throw new Error((await res.json()).detail||res.status);
    await refresh();select(id);
    const req=state.requests.find(r=>r.request_id===id);
    toast(req&&req.status==="trial_passed"?"Trial passed":"Trial failed — see the log below",!(req&&req.status==="trial_passed"));
  }catch(e){toast("Trial failed to start: "+e.message,true);}}

async function submitForReview(id){
  try{const res=await fetch(`/ops/onboard/requests/${encodeURIComponent(id)}/submit`,{method:"POST"});
    if(!res.ok)throw new Error((await res.json()).detail||res.status);
    toast("Submitted for platform review");await refresh();select(id);
  }catch(e){toast("Submit failed: "+e.message,true);}}

function select(id){selected=id;renderDetail();reqRows();}
async function refresh(){try{const res=await fetch("/ops/onboard/state");
    if(res.status===403){$("main").innerHTML=`<div class="empty">Open this page from your team's share link
      (create one from the "New team" box on <a href="/ops">/ops</a>).</div>`;return;}
    state=await res.json();$("#teamName").textContent=state.team?`team ${state.team.name}`:"";
    reqRows();renderDetail();
  }catch(e){}}
refresh();setInterval(refresh,4000);
</script></body></html>"""

_ADMIN_PAGE = r"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark light"><title>Holodeck — Onboarding review</title>
<style>""" + _TOKENS + r"""</style></head><body>
<header>
  <div class="brand"><span class="mk">◇</span><span>Holodeck<small>Onboarding review</small></span></div>
  <div class="hmeta"><a class="btn ghost" href="/ops">← back to console</a></div>
</header>
<main>
  <section class="card">
    <div class="card-h"><h2>Pending platform review</h2><span class="sub" id="countSub" style="color:var(--ink-3);font-size:12px"></span></div>
    <div class="card-b"><table>
      <thead><tr><th>App</th><th>Team</th><th style="text-align:right">Actions</th></tr></thead>
      <tbody id="reqRows"></tbody>
    </table></div>
  </section>
  <section class="card" id="detailCard" style="display:none">
    <div class="card-h"><h2 id="detailTitle">Request</h2>
      <div class="acts">
        <button class="btn ghost" onclick="reject()">Reject</button>
        <button class="btn" onclick="approve()">Approve &amp; publish</button>
      </div>
    </div>
    <div class="card-b" id="detailBody"></div>
  </section>
</main>
<div class="toast" id="toast"></div>
<script>""" + _JS_COMMON + r"""
let state={requests:[],manifest_fields:[],destructive_fields:[]}, selected=null;

function fieldRows(req){return state.manifest_fields.map(name=>{
  const f=req.manifest[name], destructive=state.destructive_fields.includes(name);
  const val=f&&f.value!=null?f.value:"";
  return `<div class="field-row">
    <div class="name">${esc(name)}${destructive?' <span title="runs a real command against the database">⚠</span>':""}</div>
    <div><span class="mono" style="font-size:12.5px">${esc(val)||'<span class=\"dim\">blank</span>'}</span></div>
    <div>${fieldBadge(f)}</div>
  </div>`;}).join("");}

function reqRows(){const tb=$("#reqRows");$("#countSub").textContent=`${state.requests.length} pending`;
  if(!state.requests.length){tb.innerHTML=`<tr><td colspan="3"><div class="empty">Nothing waiting on review.</div></td></tr>`;return;}
  tb.innerHTML=state.requests.map(r=>`<tr class="${selected===r.request_id?"sel":""}" onclick="select('${esc(r.request_id)}')">
    <td><b>${esc(r.app_name)}</b></td><td>${esc(r.team_slug)} <span style="color:var(--ink-3)">(${esc(r.contact)})</span></td>
    <td style="text-align:right"><span style="font-size:11.5px;color:var(--ink-3)">click to review</span></td></tr>`).join("");}

function renderDetail(){const card=$("#detailCard");
  const req=state.requests.find(r=>r.request_id===selected);
  if(!req){card.style.display="none";return;}
  card.style.display="";
  $("#detailTitle").textContent=`${req.app_name} · team ${req.team_slug}`;
  const log=req.trial_log?`<p class="h" style="margin-top:14px">Trial build log</p><pre class="log">${esc(req.trial_log)}</pre>`:"";
  const destructiveNote=req.destructive_unreviewed
    ?`<div class="warn-box">Unreviewed destructive field(s) — approve is blocked until the team touches them.</div>`:"";
  $("#detailBody").innerHTML=`${destructiveNote}${fieldRows(req)}${log}`;}

function select(id){selected=id;renderDetail();reqRows();}

async function approve(){if(!selected)return;
  try{const res=await fetch(`/ops/admin/onboarding/requests/${encodeURIComponent(selected)}/approve`,{method:"POST"});
    if(!res.ok)throw new Error((await res.json()).detail||res.status);
    toast("Published — the app is now in the allowlist");selected=null;await refresh();
  }catch(e){toast("Approve failed: "+e.message,true);}}

async function reject(){if(!selected)return;
  const reason=prompt("Reason (shown to the team):");if(reason==null)return;
  try{const res=await fetch(`/ops/admin/onboarding/requests/${encodeURIComponent(selected)}/reject`,{method:"POST",
      headers:{"content-type":"application/json"},body:JSON.stringify({reason})});
    if(!res.ok)throw new Error((await res.json()).detail||res.status);
    toast("Rejected — sent back to the team");selected=null;await refresh();
  }catch(e){toast("Reject failed: "+e.message,true);}}

async function refresh(){try{state=await (await fetch("/ops/admin/onboarding/state")).json();reqRows();renderDetail();}catch(e){}}
refresh();setInterval(refresh,4000);
</script></body></html>"""
