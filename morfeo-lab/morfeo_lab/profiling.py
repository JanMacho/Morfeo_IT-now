"""
JVM profiling during every scenario (async-profiler, loaded as a native agent when the JVM starts).

The agent runs all the time in every replica and writes one *collapsed stacks* file per chunk
(`monitoring.profiling.chunk`, 10 s by default) into a volume shared with the `morfeo-tools` container. Nothing
attaches to the JVM later, so it needs no privileges and works with any image that has glibc. For each scenario the
lab keeps the chunks inside its steady state, merges every replica and writes:

    profile/<event>.collapsed     the merged stacks (a standard format: speedscope, async-profiler converters)
    profile/flamegraph.html       a self-contained, interactive flame graph
    summary.json -> profile       samples, CPU by component, hottest methods, application methods
"""
from __future__ import annotations

import contextlib
import html
import json
import re
import shlex
from collections import Counter
from pathlib import Path

from .units import parse_duration

PROFILE_DIR = "/morfeo-profile"
AGENT_LIB = "/morfeo-agent/libasyncProfiler.so"
EVENT_TEXT = {"cpu": "CPU time", "wall": "wall-clock time", "alloc": "memory allocation", "lock": "lock contention"}


# ===================================================================== agent options
def enabled(lab: dict, target_data: dict, client_image: bool) -> bool:
    """`metrics.profiling` in the application file wins; otherwise it is on for images the lab builds (a known glibc
    JRE) and off for ready-made images (the agent library needs glibc, which an Alpine image may not have)."""
    mon = lab["monitoring"]
    prof = mon["profiling"]
    if not mon["enabled"] or not prof["enabled"]:
        return False
    choice = target_data["metrics"]["profiling"]
    return bool(choice) if choice is not None else not client_image


def agent_option(lab: dict) -> str:
    """-agentpath for JAVA_TOOL_OPTIONS."""
    prof = lab["monitoring"]["profiling"]
    event = prof["event"]
    chunk = int(parse_duration(prof["chunk"]))
    opts = ["start"]
    if event == "cpu":
        # ctimer: per-thread CPU timers; needs no perf_events, which containers usually block
        opts += ["event=ctimer", f"interval={prof['interval']}"]
    elif event == "wall":
        opts += ["event=wall", f"interval={prof['interval']}"]
    elif event == "alloc":
        opts += ["event=alloc", "alloc=512k"]
    else:
        opts += ["event=lock", "lock=10us"]
    opts += [f"loop={chunk}s", "collapsed", "ann", f"file={PROFILE_DIR}/{event}-%t.collapsed"]
    return f"-agentpath:{AGENT_LIB}=" + ",".join(opts)


# ===================================================================== collection
def _sh(kube, ns: str, pod: str, script: str, timeout: int = 30) -> str:
    return kube.exec(ns, pod, "sh", "-c", script, container="morfeo-tools", check=False, timeout=timeout)


def clear(kube, ns: str, pods: list[str]) -> dict[str, float]:
    """Remove old chunks (idle time, previous scenarios). Returns each pod's clock (epoch s) so that the
    test window can later be expressed in the clock of the node that wrote the files."""
    clocks = {}
    for pod in pods:
        txt = _sh(kube, ns, pod, f"rm -f {PROFILE_DIR}/*.collapsed; date +%s")
        with contextlib.suppress(ValueError, IndexError):
            clocks[pod] = float(txt.strip().splitlines()[-1])
    return clocks


def collect(kube, ns: str, pods: list[str], window: tuple[float, float], offsets: dict[str, float],
            chunk: float) -> dict[str, Counter]:
    """Stacks per replica for the chunks that overlap `window` (local epoch seconds) by at least half."""
    out: dict[str, Counter] = {}
    for pod in pods:
        off = offsets.get(pod, 0.0)
        lo, hi = window[0] + off, window[1] + off
        listing = _sh(kube, ns, pod, f"cd {PROFILE_DIR} 2>/dev/null && for f in *.collapsed; do "
                                     '[ -f "$f" ] && echo "$(stat -c %Y "$f") $f"; done')
        chosen = []
        for line in listing.splitlines():
            parts = line.split(" ", 1)
            if len(parts) != 2 or not parts[0].isdigit():
                continue
            end = float(parts[0])
            start = end - chunk
            overlap = min(end, hi) - max(start, lo)
            if overlap >= chunk / 2:
                chosen.append(parts[1].strip())
        if not chosen:
            continue
        data = _sh(kube, ns, pod, f"cd {PROFILE_DIR} && cat " + " ".join(shlex.quote(f) for f in chosen),
                   timeout=120)
        out[pod] = parse_collapsed(data)
    return out


def parse_collapsed(text: str) -> Counter:
    c: Counter = Counter()
    for line in text.splitlines():
        stack, _, n = line.rstrip().rpartition(" ")
        if stack and n.isdigit():
            c[stack] += int(n)
    return c


# ===================================================================== frames
_SUFFIX = re.compile(r"_\[([jik0-9])\]$")
LIBRARY_PREFIXES = (
    "java/", "javax/", "jakarta/", "jdk/", "sun/", "com/sun/", "kotlin/", "kotlinx/", "scala/",
    "org/springframework/", "org/apache/", "org/eclipse/", "org/glassfish/", "org/jboss/", "org/hibernate/",
    "org/postgresql/", "org/h2/", "org/hsqldb/", "org/mariadb/", "com/mysql/", "oracle/", "com/oracle/",
    "com/zaxxer/", "com/fasterxml/", "com/google/", "ch/qos/", "org/slf4j/", "org/yaml/", "com/ctc/",
    "io/netty/", "io/micrometer/", "io/javalin/", "io/undertow/", "io/prometheus/", "io/opentelemetry/",
    "io/vertx/", "io/quarkus/", "io/smallrye/", "io/micronaut/", "io/projectreactor/", "reactor/",
    "net/bytebuddy/", "org/aspectj/", "org/jetbrains/", "okhttp3/", "okio/", "org/thymeleaf/", "lombok/",
    "org/json/", "org/reactivestreams/", "org/objectweb/", "org/codehaus/", "org/xml/", "org/w3c/",
)

# Walking from the leaf towards the root: the first frame of a "domain" library decides (a String.charAt
# inside Jackson is JSON work, a socket read inside the JDBC driver is database access); socket and file
# frames make the rest of the stack network work; core JDK frames are transparent.
DOMAINS = (
    ("Database access", ("org/postgresql/", "com/mysql/", "oracle/jdbc/", "org/mariadb/", "org/h2/",
                         "com/zaxxer/hikari/", "org/hibernate/", "org/springframework/jdbc/",
                         "org/springframework/orm/", "org/springframework/data/", "java/sql/", "javax/sql/")),
    ("JSON and serialization", ("com/fasterxml/", "com/google/gson/", "jakarta/json/", "org/json/",
                                "org/springframework/http/converter/")),
    ("Logging", ("ch/qos/logback/", "org/apache/logging/log4j/", "org/slf4j/", "java/util/logging/")),
    ("Lab agents", ("io/prometheus/", "one/profiler/")),
)
NETWORK = ("sun/nio/ch/", "java/net/", "sun/net/", "java/nio/channels/", "io/netty/channel/",
           "org/apache/tomcat/util/net/", "java/io/FileOutputStream", "java/io/FileInputStream",
           "java/io/RandomAccessFile", "sun/nio/fs/", "jdk/internal/net/")
WEB = ("org/apache/catalina/", "org/apache/coyote/", "org/apache/tomcat/", "org/springframework/",
       "org/eclipse/jetty/", "io/javalin/", "io/undertow/", "io/netty/", "io/vertx/", "io/quarkus/",
       "io/micronaut/", "jakarta/servlet/", "javax/servlet/", "org/glassfish/", "com/sun/net/httpserver/",
       "sun/net/httpserver/")
CORE_TRANSPARENT = ("java/", "javax/", "jdk/", "sun/", "com/sun/", "kotlin/", "scala/")
IO_NATIVE = re.compile(r"^(__)?(libc_)?(epoll_wait|epoll_pwait|poll|read|write|recv|recvfrom|send|sendto|"
                       r"writev|readv|accept4?|connect|__libc_(read|write|recv|send)|NET_\w+|socket\w*)\b")
GC_NATIVE = re.compile(r"(G1|GC|Gang|ParallelScavenge|PSPromotion|ZGC|Shenandoah|ConcurrentMark|"
                       r"MarkSweep|PSScavenge|Universe::heap|CollectedHeap|WorkerThread)")
JIT_NATIVE = re.compile(r"(CompileBroker|C2Compiler|Compile::|C1_|Compilation::|JVMCI)")
OTHER = "Other JVM and native code"
APP = "Application code"
JDK = "JDK library"
LIBS = "Other libraries"


def frame_kind(raw: str) -> str:
    """'java' | 'native' | 'kernel' from the async-profiler `ann` suffix."""
    m = _SUFFIX.search(raw)
    if not m:
        return "native"
    return "kernel" if m.group(1) == "k" else "java"


_SHADED = re.compile(r"^[0-9a-f]{32}/")                  # jmx_exporter 1.x shades its dependencies like this
_HIDDEN = re.compile(r"\$\$Lambda[.$/]0x[0-9a-f]+|\$\$Lambda\$\d+/0x[0-9a-f]+")   # lambda classes: one per JVM
_ADDR = re.compile(r"\.0x[0-9a-f]{6,}")


def _base(raw: str) -> str:
    """Frame without annotation, shading prefix or the address of hidden (lambda) classes."""
    name = _SHADED.sub("", _SUFFIX.sub("", raw))
    return _ADDR.sub("", _HIDDEN.sub("$$Lambda", name))


def clean(raw: str) -> str:
    name = _base(raw)
    if frame_kind(raw) == "java":
        return name.replace("/", ".")
    if name.startswith("/"):                               # unresolved native frame: the library file
        return name.rsplit("/", 1)[-1]
    return name


def is_app(raw_java: str) -> bool:
    if _SHADED.match(_SUFFIX.sub("", raw_java)):
        return False
    return not _base(raw_java).startswith(LIBRARY_PREFIXES)


def category_of(frames: list[str]) -> str:
    last = max((i for i, f in enumerate(frames) if frame_kind(f) == "java"), default=-1)
    if last < 0:
        joined = ";".join(frames)
        if JIT_NATIVE.search(joined):
            return "JIT compiler"
        if GC_NATIVE.search(joined):
            return "Garbage collection"
        return OTHER
    java = [f for f in frames[:last + 1] if frame_kind(f) == "java"]
    # native work below the leaf-most Java frame: GC in an allocation slow path, socket syscalls
    tail_names = [_SUFFIX.sub("", f) for f in frames[last + 1:]]
    if any(GC_NATIVE.search(n) for n in tail_names):
        return "Garbage collection"
    net = any(IO_NATIVE.match(n) for n in tail_names)
    for f in reversed(java):
        name = _base(f)
        for cat, prefixes in DOMAINS:
            if name.startswith(prefixes):
                return cat
        if name.startswith(NETWORK):
            net = True
        elif name.startswith(WEB):
            return "Network and I/O" if net else "Web server and framework"
        elif not name.startswith(CORE_TRANSPARENT):
            return "Network and I/O" if net else (APP if is_app(f) else LIBS)
    return "Network and I/O" if net else JDK


def analyze(stacks: Counter, event: str, top: int = 15) -> dict:
    total = sum(stacks.values())
    if not total:
        return {"available": False, "reason": "no samples"}
    cats: Counter = Counter()
    self_time: Counter = Counter()
    app_total: Counter = Counter()
    for stack, n in stacks.items():
        frames = stack.split(";")
        cats[category_of(frames)] += n
        java = [f for f in frames if frame_kind(f) == "java"]
        leaf = clean(java[-1]) if java else clean(frames[-1]) + " (native)"
        self_time[leaf] += n
        for name in {clean(f) for f in java if is_app(f)}:
            app_total[name] += n

    def rows(counter):
        return [{"method": k, "samples": v, "pct": round(100.0 * v / total, 2)} for k, v in counter.most_common(top)]

    return {
        "available": True, "event": event, "samples": total,
        "categories": [{"name": k, "samples": v, "pct": round(100.0 * v / total, 2)} for k, v in cats.most_common()],
        "self_top": rows(self_time),
        "app_top": rows(app_total),
    }


# ===================================================================== outputs
def write_outputs(out_dir: Path, per_pod: dict[str, Counter], event: str, title: str, window_s: float) -> dict:
    merged: Counter = Counter()
    for c in per_pod.values():
        merged.update(c)
    res = analyze(merged, event)
    if not res.get("available"):
        return res
    d = out_dir / "profile"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{event}.collapsed").write_text("".join(f"{s} {n}\n" for s, n in merged.most_common()), encoding="utf-8")
    write_flamegraph(d / "flamegraph.html", merged, title=title,
                     subtitle=f"{EVENT_TEXT.get(event, event)} · {res['samples']:,} samples · "
                              f"{len(per_pod)} replica{'s' if len(per_pod) != 1 else ''} · "
                              f"{window_s:.0f} s of steady load")
    res.update({"replicas": len(per_pod), "window_s": round(window_s, 1),
                "files": {"flamegraph": "profile/flamegraph.html", "collapsed": f"profile/{event}.collapsed"}})
    return res


def _tree(stacks: Counter, min_share: float = 0.00005) -> tuple[list[str], list[str], list]:
    """Prefix tree flattened for the page: names, kinds, root node [frame, total, [children...]]."""
    names: list[str] = []
    kinds: list[str] = []
    index: dict[str, int] = {}
    root = {"n": 0, "c": {}}
    for stack, n in stacks.items():
        node = root
        node["n"] += n
        for f in stack.split(";"):
            child = node["c"].get(f)
            if child is None:
                child = node["c"][f] = {"n": 0, "c": {}}
            child["n"] += n
            node = child
    total = root["n"] or 1

    def ref(raw):
        if raw not in index:
            index[raw] = len(names)
            names.append(clean(raw))
            kinds.append(_kind_code(raw))
        return index[raw]

    def flat(raw, node):
        kids = [flat(k, v) for k, v in sorted(node["c"].items(), key=lambda kv: -kv[1]["n"])
                if v["n"] / total >= min_share]
        return [ref(raw) if raw is not None else -1, node["n"], kids]

    return names, kinds, flat(None, root)


def _kind_code(raw: str) -> str:
    k = frame_kind(raw)
    if k == "kernel":
        return "k"
    if k == "native":
        name = _SUFFIX.sub("", raw)
        return "g" if GC_NATIVE.search(name) or JIT_NATIVE.search(name) else "n"
    if is_app(raw):
        return "a"
    return "j" if _base(raw).startswith(CORE_TRANSPARENT) else "l"


def write_flamegraph(path: Path, stacks: Counter, *, title: str, subtitle: str):
    names, kinds, tree = _tree(stacks)
    data = json.dumps({"names": names, "kinds": "".join(kinds), "tree": tree}, separators=(",", ":"))
    page = FLAME_HTML.replace("__TITLE__", html.escape(title)).replace("__SUBTITLE__", html.escape(subtitle))
    page = page.replace("__DATA__", data.replace("</", "<\\/"))
    path.write_text(page, encoding="utf-8")


FLAME_HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__ · flame graph</title>
<style>
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink-2:#52514e;--muted:#898781;--ring:rgba(11,11,11,.12);
 --hl:#d03b3b;--a:#3f9b5a;--l:#3a7fc4;--j:#5aa6a0;--n:#d08a2c;--g:#8a63c9;--k:#c45a4a}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;
 --ink-2:#c3c2b7;--muted:#898781;--ring:rgba(255,255,255,.14);--hl:#ff6b6b}}
:root[data-theme="dark"]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#fff;--ink-2:#c3c2b7;--muted:#898781;
 --ring:rgba(255,255,255,.14);--hl:#ff6b6b}
*{box-sizing:border-box} html,body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{padding:20px 16px 40px;max-width:1600px;margin:0 auto}
h1{font-size:20px;margin:0;font-weight:650} .sub{color:var(--ink-2);margin:2px 0 14px}
.brand{font-size:11.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--ink-2);margin-bottom:6px;font-weight:600}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
input{font:inherit;padding:6px 10px;border-radius:8px;border:1px solid var(--ring);background:var(--surface);color:var(--ink);min-width:260px}
button{font:inherit;font-size:13px;padding:6px 12px;border-radius:8px;border:1px solid var(--ring);background:var(--surface);color:var(--ink-2);cursor:pointer}
.info{color:var(--ink-2);font-size:13px;min-height:20px}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--ink-2);margin:0 0 10px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px}
#wrap{position:relative;border:1px solid var(--ring);border-radius:12px;background:var(--surface);overflow:hidden}
canvas{display:block;width:100%}
#tip{position:fixed;pointer-events:none;background:var(--surface);color:var(--ink);border:1px solid var(--ring);border-radius:8px;
 padding:8px 10px;font-size:12.5px;max-width:640px;box-shadow:0 4px 16px rgba(0,0,0,.15);display:none;word-break:break-all}
#tip b{font-weight:600}
</style></head><body><main>
<div class="brand">MORFEO · flame graph</div><h1>__TITLE__</h1><p class="sub">__SUBTITLE__</p>
<div class="bar"><input id="q" placeholder="Search (text or /regex/)" autocomplete="off">
<button id="reset" type="button">Reset zoom</button><button id="theme" type="button" title="Light / dark">◐</button>
<span class="info" id="info"></span></div>
<div class="legend"><span><i style="background:var(--a)"></i>application</span><span><i style="background:var(--l)"></i>libraries and frameworks</span>
<span><i style="background:var(--j)"></i>JDK</span><span><i style="background:var(--n)"></i>JVM and native</span>
<span><i style="background:var(--g)"></i>GC and JIT</span><span><i style="background:var(--k)"></i>kernel</span></div>
<div id="wrap"><canvas id="c"></canvas></div><div id="tip"></div>
</main>
<script>
const D=__DATA__;
const H=18, cv=document.getElementById('c'), ctx=cv.getContext('2d'), tip=document.getElementById('tip');
const info=document.getElementById('info'), q=document.getElementById('q');
const root=D.tree, TOTAL=root[1]||1;
function name(n){return n[0]<0?'all':D.names[n[0]]}
function kind(n){return n[0]<0?'r':D.kinds[n[0]]}
let depth=0;(function d(n,l){depth=Math.max(depth,l);for(const c of n[2])d(c,l+1)})(root,0);
let zoom=root, path=[root], match=null, boxes=[];
function css(v){return getComputedStyle(document.documentElement).getPropertyValue(v).trim()}
function shade(hex,seed){const n=parseInt(hex.slice(1),16);let r=n>>16,g=n>>8&255,b=n&255;const f=0.86+((seed%7)/7)*0.22;
 return `rgb(${Math.min(255,r*f|0)},${Math.min(255,g*f|0)},${Math.min(255,b*f|0)})`}
function hash(s){let h=0;for(let i=0;i<s.length;i++)h=(h*31+s.charCodeAt(i))|0;return Math.abs(h)}
function matches(n){if(!match||n[0]<0)return false;const s=name(n);return match instanceof RegExp?match.test(s):s.includes(match)}
function draw(){
 const W=cv.parentElement.clientWidth, dpr=window.devicePixelRatio||1, rows=depth+1;
 cv.width=W*dpr; cv.height=rows*H*dpr; cv.style.height=rows*H+'px'; ctx.setTransform(dpr,0,0,dpr,0,0);
 ctx.clearRect(0,0,W,rows*H); boxes=[]; ctx.font='12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace'; ctx.textBaseline='middle';
 const colors={a:css('--a'),l:css('--l'),j:css('--j'),n:css('--n'),g:css('--g'),k:css('--k'),r:css('--muted')}, hl=css('--hl');
 let matched=0;
 // ancestors of the zoomed frame: full width
 path.slice(0,-1).forEach((n,i)=>box(n,0,W,i,true));
 function box(n,x,w,level,dim){
  const y=level*H, m=matches(n);
  ctx.globalAlpha=dim?0.55:1; ctx.fillStyle=m?hl:shade(colors[kind(n)]||colors.n,hash(name(n)));
  ctx.fillRect(x,y,Math.max(w-1,0.5),H-1); ctx.globalAlpha=1;
  if(w>36){ctx.fillStyle='#fff';const t=name(n);let s=t;const max=Math.floor((w-8)/7.2);if(s.length>max)s=s.slice(0,Math.max(0,max-1))+'…';ctx.fillText(s,x+4,y+H/2)}
  boxes.push([x,y,w,n]);
 }
 (function rec(n,x,w,level){
  box(n,x,w,level,false); if(matches(n))matched+=n[1];
  let cx=x;for(const c of n[2]){const cw=w*c[1]/n[1];if(cw>=0.4)rec(c,cx,cw,level+1);cx+=cw}
 })(zoom,0,W,path.length-1);
 info.textContent=match?`Matched: ${(100*matched/TOTAL).toFixed(2)}% of samples`:(zoom!==root?`Zoomed: ${name(zoom)} (${(100*zoom[1]/TOTAL).toFixed(2)}%)`:'');
}
function at(e){const r=cv.getBoundingClientRect(),x=e.clientX-r.left,y=e.clientY-r.top;
 for(let i=boxes.length-1;i>=0;i--){const b=boxes[i];if(x>=b[0]&&x<b[0]+b[2]&&y>=b[1]&&y<b[1]+H)return b[3]}return null}
function findPath(target){const p=[];(function f(n){p.push(n);if(n===target)return true;for(const c of n[2])if(f(c))return true;p.pop();return false})(root);return p}
cv.addEventListener('mousemove',e=>{const n=at(e);if(!n){tip.style.display='none';return}
 const parent=path.includes(n)?null:findPath(n).slice(-2)[0];
 tip.innerHTML=`<b></b><br>${n[1].toLocaleString()} samples · ${(100*n[1]/TOTAL).toFixed(2)}% of total`+(parent&&parent!==n?` · ${(100*n[1]/parent[1]).toFixed(1)}% of caller`:'');
 tip.querySelector('b').textContent=name(n);tip.style.display='block';
 const x=Math.min(e.clientX+14,window.innerWidth-tip.offsetWidth-8);tip.style.left=x+'px';tip.style.top=(e.clientY+14)+'px'});
cv.addEventListener('mouseleave',()=>tip.style.display='none');
cv.addEventListener('click',e=>{const n=at(e);if(!n)return;zoom=n;path=findPath(n);draw()});
document.getElementById('reset').onclick=()=>{zoom=root;path=[root];draw()};
document.getElementById('theme').onclick=()=>{const r=document.documentElement;const dark=r.dataset.theme?r.dataset.theme==='dark':matchMedia('(prefers-color-scheme: dark)').matches;r.dataset.theme=dark?'light':'dark';draw()};
q.addEventListener('input',()=>{const v=q.value.trim();if(!v){match=null}else if(v.length>2&&v.startsWith('/')&&v.endsWith('/')){try{match=new RegExp(v.slice(1,-1))}catch(_){match=null}}else{match=v}draw()});
window.addEventListener('resize',draw);draw();
</script></body></html>
"""
