import { useState, useEffect } from "react";

// ── Global CSS ────────────────────────────────────────────────────────────────
const CSS = `
@import url('https://fonts.googleapis.com/css2?family=Syne:wght@700;800&family=IBM+Plex+Mono:wght@400;500&display=swap');

*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}

@keyframes fadeUp {
  from { opacity:0; transform:translateY(18px); }
  to   { opacity:1; transform:translateY(0); }
}
@keyframes flowDot {
  0%   { left:-4%;  opacity:0; }
  8%   { opacity:1; }
  92%  { opacity:1; }
  100% { left:104%; opacity:0; }
}
@keyframes pulseGlow {
  0%,100% { box-shadow:0 0 0 0 rgba(139,92,246,0); }
  50%     { box-shadow:0 0 16px 2px rgba(139,92,246,0.25); }
}
@keyframes bgShift {
  0%   { background-position:0 0; }
  100% { background-position:40px 40px; }
}
@keyframes badgePulse {
  0%,100% { opacity:1; }
  50%     { opacity:0.4; }
}
@keyframes scanline {
  0%   { top:0%;    opacity:0.03; }
  50%  { opacity:0.06; }
  100% { top:100%;  opacity:0.03; }
}

.vf-root {
  font-family:'IBM Plex Mono',monospace;
  background:#05050e;
  min-height:100vh;
  padding:40px 36px 56px;
  position:relative;
  overflow:hidden;
  color:#c8d3f5;
}
.vf-root::before {
  content:'';
  position:fixed;inset:0;
  background-image:
    linear-gradient(rgba(100,80,255,0.045) 1px,transparent 1px),
    linear-gradient(90deg,rgba(100,80,255,0.045) 1px,transparent 1px);
  background-size:44px 44px;
  animation:bgShift 8s linear infinite;
  pointer-events:none;
  z-index:0;
}
.vf-root::after {
  content:'';
  position:fixed;
  left:0;right:0;height:120px;
  background:linear-gradient(180deg,rgba(0,0,10,0.4) 0,transparent 100%);
  top:0;z-index:0;pointer-events:none;
}
.scan {
  position:fixed;left:0;right:0;height:2px;
  background:linear-gradient(90deg,transparent,rgba(139,92,246,0.08),transparent);
  animation:scanline 6s linear infinite;
  pointer-events:none;z-index:1;
}
.z1 { position:relative;z-index:1; }

/* ── Header ── */
.hdr { animation:fadeUp 0.7s ease both; }
.brand {
  font-family:'Syne',sans-serif;
  font-size:clamp(32px,5vw,52px);
  font-weight:800;
  letter-spacing:-1.5px;
  background:linear-gradient(125deg,#fff 0%,#c4b5fd 55%,#60a5fa 100%);
  -webkit-background-clip:text;
  -webkit-text-fill-color:transparent;
  background-clip:text;
  line-height:1;margin-bottom:8px;
}
.tagline {
  font-size:10px;letter-spacing:3px;text-transform:uppercase;
  color:#4a5280;margin-bottom:24px;
}
.stats {
  display:flex;gap:12px;flex-wrap:wrap;
}
.stat {
  display:flex;align-items:center;gap:8px;
  padding:5px 14px;
  border:1px solid rgba(120,100,255,0.18);
  border-radius:4px;background:rgba(13,13,35,0.8);
  font-size:11px;color:#4a5280;
  transition:border-color 0.2s;
}
.stat:hover{border-color:rgba(139,92,246,0.4);}
.stat-n{font-size:15px;font-weight:500;color:#e2e8f0;}

/* ── Section headers ── */
.sec-hdr {
  display:flex;align-items:center;gap:12px;
  font-size:10px;letter-spacing:2.5px;text-transform:uppercase;
  color:#3a4070;margin-bottom:20px;
}
.sec-hdr::after {
  content:'';flex:1;height:1px;
  background:linear-gradient(90deg,rgba(100,80,255,0.15) 0,transparent 100%);
}
.tag {
  display:inline-flex;align-items:center;gap:5px;
  padding:3px 10px;border-radius:4px;
  font-size:9px;font-weight:500;letter-spacing:1.5px;
  text-transform:uppercase;
}
.tag-c{background:rgba(34,211,238,0.1);color:#22d3ee;border:1px solid rgba(34,211,238,0.2);}
.tag-e{background:rgba(16,185,129,0.1);color:#10b981;border:1px solid rgba(16,185,129,0.2);}
.tag-v{background:rgba(139,92,246,0.1);color:#8b5cf6;border:1px solid rgba(139,92,246,0.2);}

/* ── Pipeline ── */
.pipe-row {
  display:flex;align-items:center;gap:0;
  overflow-x:auto;padding-bottom:8px;
}
.pipe-row::-webkit-scrollbar{height:2px;}
.pipe-row::-webkit-scrollbar-thumb{background:rgba(100,80,255,0.2);border-radius:2px;}

/* ── Nodes ── */
.node {
  flex-shrink:0;
  padding:11px 14px;
  border:1px solid rgba(120,100,255,0.18);
  border-radius:8px;
  background:rgba(13,13,35,0.9);
  min-width:110px;max-width:150px;
  position:relative;
  cursor:default;
  transition:all 0.22s ease;
}
.node:hover{transform:translateY(-3px);}
.node-ico{font-size:15px;margin-bottom:5px;line-height:1;}
.node-name{font-size:11px;font-weight:500;line-height:1.3;}
.node-sub{font-size:9px;color:#4a5280;margin-top:3px;line-height:1.3;}

.node-c{border-color:rgba(34,211,238,0.25);}
.node-c .node-name{color:#7ae8f7;}
.node-c:hover{border-color:#22d3ee;box-shadow:0 0 24px rgba(34,211,238,0.12);}

.node-e{border-color:rgba(16,185,129,0.25);}
.node-e .node-name{color:#6ee7b7;}
.node-e:hover{border-color:#10b981;box-shadow:0 0 24px rgba(16,185,129,0.12);}

.node-v{border-color:rgba(139,92,246,0.25);}
.node-v .node-name{color:#c4b5fd;}
.node-v:hover{border-color:#8b5cf6;box-shadow:0 0 24px rgba(139,92,246,0.15);animation:pulseGlow 2s ease infinite;}

.node-dsh{border-style:dashed;opacity:0.85;}

.badge {
  position:absolute;top:-3px;right:-3px;
  width:7px;height:7px;border-radius:50%;
  border:1px solid #05050e;
}
.badge-done{background:#10b981;box-shadow:0 0 6px #10b981;}
.badge-pend{background:#f59e0b;box-shadow:0 0 6px #f59e0b;animation:badgePulse 2s ease infinite;}

/* ── Connector ── */
.conn {
  flex-shrink:0;position:relative;
  width:44px;height:24px;
  display:flex;align-items:center;
  overflow:hidden;
}
.conn-line{position:absolute;width:100%;height:1px;}
.conn-arrow{
  position:absolute;right:0;
  border-left-width:6px;border-left-style:solid;
  border-top:4px solid transparent;
  border-bottom:4px solid transparent;
}
.conn-dot{
  position:absolute;
  width:6px;height:6px;border-radius:50%;
  top:50%;margin-top:-3px;
  animation:flowDot 2.4s linear infinite;
}

/* ── LangGraph box ── */
.lg-box {
  position:relative;
  padding:14px 12px 10px;
  border:1px dashed rgba(139,92,246,0.3);
  border-radius:10px;
  background:rgba(139,92,246,0.04);
  flex-shrink:0;
}
.lg-label {
  position:absolute;top:-10px;left:50%;transform:translateX(-50%);
  white-space:nowrap;
  font-size:8.5px;letter-spacing:2px;text-transform:uppercase;
  color:#8b5cf6;background:#05050e;padding:0 8px;
}
.lg-inner{display:flex;align-items:center;gap:0;}

/* ── Status grid ── */
.status-grid {
  display:grid;
  grid-template-columns:repeat(auto-fill,minmax(185px,1fr));
  gap:7px;
}
.status-card {
  display:flex;align-items:center;gap:9px;
  padding:8px 11px;
  border:1px solid rgba(100,80,255,0.13);
  border-radius:6px;background:rgba(13,13,35,0.7);
  font-size:10px;
  transition:all 0.2s;
}
.status-card:hover{
  border-color:rgba(139,92,246,0.3);
  background:rgba(13,13,35,1);
  transform:translateY(-1px);
}
.sdot{width:5px;height:5px;border-radius:50%;flex-shrink:0;}
.sdot-done{background:#10b981;box-shadow:0 0 5px #10b981;}
.sdot-pend{background:#f59e0b;box-shadow:0 0 5px #f59e0b;}
.sfile{color:#4a5280;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;}
.slabel{font-size:8.5px;color:#2e3456;text-transform:uppercase;letter-spacing:1px;margin-top:1px;}

.divider{
  height:1px;
  background:linear-gradient(90deg,transparent,rgba(100,80,255,0.18),transparent);
  margin:36px 0;position:relative;z-index:1;
}

.stacked{display:flex;flex-direction:column;gap:8px;flex-shrink:0;}
`;

// ── Data ─────────────────────────────────────────────────────────────────────

const FILES = [
  {f:"api/main.py",       l:"FastAPI + Routing",   s:"done"},
  {f:"api/config.py",     l:"Settings (pydantic)",  s:"done"},
  {f:"api/schemas.py",    l:"Pydantic v2 Models",   s:"done"},
  {f:"api/dependencies.py",l:"Auth Middleware",     s:"done"},
  {f:"api/logger.py",     l:"structlog JSON",       s:"done"},
  {f:"api/retriever.py",  l:"RetrieverAgent",       s:"done"},
  {f:"api/agent.py",      l:"LangGraph RAGAgent",   s:"done"},
  {f:"ingestion/lambda_s3.py",   l:"S3 Trigger",    s:"done"},
  {f:"ingestion/lambda_parser.py",l:"Parser Lambda",s:"done"},
  {f:"ingestion/lambda_webhook.py",l:"Webhook Lambda",s:"done"},
  {f:"ingestion/parser.py",  l:"Doc Parser",        s:"done"},
  {f:"ingestion/chunker.py", l:"Text Chunker",      s:"done"},
  {f:"ingestion/models.py",  l:"TextBlock + Chunk", s:"done"},
  {f:"embeddings/lambda_embed.py",l:"EmbeddingAgent",s:"done"},
  {f:"scripts/demo.py",      l:"Demo Script",       s:"done"},
  {f:"tests/test_api.py",    l:"API Test Suite",    s:"done"},
  {f:"tests/test_ingestion.py",l:"Ingestion Tests", s:"done"},
  {f:"tests/test_embedding.py",l:"Embedding Tests", s:"done"},
  {f:"tests/test_webhook.py",  l:"Webhook Tests",   s:"done"},
  {f:"tests/test_rag.py",    l:"RAG Tests",         s:"done"},
  {f:"infra/Dockerfile",     l:"Container Image",   s:"pend"},
  {f:"infra/ecs_task.json",  l:"ECS Task Def",      s:"pend"},
  {f:"infra/deploy.sh",      l:"Deploy Script",     s:"pend"},
];

// ── Sub-components ────────────────────────────────────────────────────────────

function Node({ ico, name, sub, c = "c", dashed = false, delay = 0, pend = false }) {
  return (
    <div
      className={`node node-${c} ${dashed?"node-dsh":""}`}
      style={{ animation: `fadeUp 0.5s ease ${delay}ms both` }}
    >
      <div className={`badge ${pend?"badge-pend":"badge-done"}`} />
      <div className="node-ico">{ico}</div>
      <div className="node-name">{name}</div>
      {sub && <div className="node-sub">{sub}</div>}
    </div>
  );
}

function Conn({ c = "cyan", delay = 0 }) {
  const COLS = { cyan:"#22d3ee", emerald:"#10b981", violet:"#8b5cf6" };
  const col = COLS[c] || COLS.cyan;
  return (
    <div className="conn">
      <div className="conn-line" style={{ background:`linear-gradient(90deg,transparent,${col}55,transparent)` }} />
      <div className="conn-arrow" style={{ borderLeftColor:`${col}55` }} />
      <span className="conn-dot" style={{ background:col, boxShadow:`0 0 8px ${col}`, animationDelay:`${delay}s` }} />
    </div>
  );
}

// ── Main ──────────────────────────────────────────────────────────────────────

export default function VecturaFlowViz() {
  const [loaded, setLoaded] = useState(false);
  useEffect(() => { setTimeout(() => setLoaded(true), 80); }, []);

  const done = FILES.filter(f => f.s === "done").length;
  const pend = FILES.filter(f => f.s === "pend").length;
  const tests = FILES.filter(f => f.f.startsWith("tests/")).length;

  return (
    <div className="vf-root">
      <style>{CSS}</style>
      <div className="scan" />

      {/* ── Header ──────────────────────────────────────────────────────── */}
      <div className="hdr z1" style={{ marginBottom:48 }}>
        <div className="brand">VecturaFlow</div>
        <div className="tagline">Autonomous Agentic RAG Platform · AWS · Sprint Architecture</div>
        <div className="stats">
          {[
            ["23", "total files"],
            [done,  "complete"],
            [tests, "test suites"],
            ["2",   "pipelines"],
            [pend,  "deploy phase"],
          ].map(([n,l],i) => (
            <div className="stat" key={i}>
              <span className="stat-n">{n}</span>{l}
            </div>
          ))}
        </div>
      </div>

      {/* ── Ingestion Pipeline ──────────────────────────────────────────── */}
      <div className="z1" style={{ marginBottom:40, animation:"fadeUp 0.7s ease 0.15s both" }}>
        <div className="sec-hdr">
          <span className="tag tag-c">▶ Ingestion Pipeline</span>
          S3 + Webhook → Parse → Chunk → Embed → Pinecone
        </div>

        <div className="pipe-row">
          {/* Dual entry */}
          <div className="stacked">
            <Node ico="🪣" name="S3 Upload"     sub="lambda_s3.py"      c="c" delay={0}  />
            <Node ico="🔔" name="Webhook POST"  sub="lambda_webhook.py" c="c" delay={80} />
          </div>

          <div className="stacked" style={{ gap:8 }}>
            <Conn c="cyan" delay={0} />
            <Conn c="cyan" delay={0.5} />
          </div>

          <div className="stacked">
            <Node ico="📬" name="SQS Ingestion" sub="parse queue"   c="c" delay={160} />
            <Node ico="⚡" name="SQS Embedding" sub="direct to embed" c="c" delay={240} />
          </div>

          <Conn c="cyan" delay={0.2} />

          <div className="stacked">
            <Node ico="📄" name="Parser Agent"  sub="pdf/docx/csv/txt/json" c="c" delay={320} />
            <div style={{ height:8 }} />
          </div>

          <div className="stacked">
            <Conn c="cyan" delay={0.4} />
            <div style={{ width:44, height:24 }} />
          </div>

          <div className="stacked">
            <Node ico="✂️" name="Chunker Agent" sub="512 chars · overlap 50" c="c" delay={400} />
            <div style={{ height:8 }} />
          </div>

          <div className="stacked">
            <Conn c="cyan" delay={0.6} />
            <Conn c="cyan" delay={0.9} />
          </div>

          <Node ico="⚡" name="Embedding Agent" sub="text-embed-3-small · batch" c="v" delay={480} />

          <div className="stacked">
            <Conn c="violet" delay={0.1} />
            <Conn c="violet" delay={0.7} />
          </div>

          <div className="stacked">
            <Node ico="📌" name="Pinecone"  sub="1536-dim vectors" c="v" dashed delay={560} />
            <Node ico="🗄️" name="DynamoDB" sub="doc registry · GSI" c="v" dashed delay={640} />
          </div>
        </div>
      </div>

      <div className="divider" />

      {/* ── Query Pipeline ──────────────────────────────────────────────── */}
      <div className="z1" style={{ marginBottom:40, animation:"fadeUp 0.7s ease 0.3s both" }}>
        <div className="sec-hdr">
          <span className="tag tag-e">▶ Query Pipeline</span>
          User → FastAPI → LangGraph RAGAgent → GPT-4o mini → Response
        </div>

        <div className="pipe-row" style={{ alignItems:"flex-start", paddingTop:8 }}>
          <Node ico="👤" name="User Query"  sub="/v1/chat/completions" c="e" delay={0} />
          <Conn c="emerald" delay={0} />
          <Node ico="🔐" name="FastAPI + Auth" sub="Bearer key → DynamoDB" c="e" delay={80} />
          <Conn c="emerald" delay={0.3} />

          {/* LangGraph box */}
          <div className="lg-box" style={{ animation:"fadeUp 0.6s ease 200ms both" }}>
            <div className="lg-label">LangGraph · 4-node StateGraph</div>
            <div className="lg-inner">
              <Node ico="🔀" name="Decompose" sub="multi-query split" c="v" delay={200} />
              <Conn c="violet" delay={0.2} />
              <Node ico="🔍" name="Retrieve"  sub="Pinecone · Redis cache" c="v" delay={280} />
              <Conn c="violet" delay={0.4} />
              <Node ico="🤖" name="Generate"  sub="GPT-4o mini · T=0" c="v" delay={360} />
              <Conn c="violet" delay={0.6} />
              <Node ico="✅" name="Validate"  sub="grounding check" c="v" delay={440} />
            </div>
          </div>

          <Conn c="emerald" delay={0.5} />
          <Node ico="💬" name="Response"  sub="answer · sources · confidence" c="e" delay={520} />
        </div>
      </div>

      <div className="divider" />

      {/* ── Build Status ────────────────────────────────────────────────── */}
      <div className="z1" style={{ animation:"fadeUp 0.7s ease 0.45s both" }}>
        <div className="sec-hdr">
          <span className="tag tag-v">▶ Build Status</span>
          {done} complete · {pend} pending deployment
        </div>

        <div className="status-grid">
          {FILES.map(({ f, l, s }) => (
            <div key={f} className="status-card">
              <div className={`sdot ${s === "done" ? "sdot-done" : "sdot-pend"}`} />
              <div style={{ flex:1, overflow:"hidden" }}>
                <div className="sfile">{f}</div>
                <div className="slabel">{l}</div>
              </div>
            </div>
          ))}
        </div>

        <div style={{ display:"flex", gap:20, marginTop:20, flexWrap:"wrap" }}>
          {[
            { col:"#10b981", label:"Complete" },
            { col:"#f59e0b", label:"Pending (deployment phase)" },
          ].map(({ col, label }) => (
            <div key={label} style={{ display:"flex", alignItems:"center", gap:7, fontSize:10, color:"#3a4070" }}>
              <div style={{ width:6, height:6, borderRadius:"50%", background:col, boxShadow:`0 0 5px ${col}` }} />
              {label}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
