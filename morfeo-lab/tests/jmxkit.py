"""A small builder of JMeter test plans (the XML that JMeter 5.6 saves), for the tests."""
from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr


def _p(kind: str, name: str, value) -> str:
    if isinstance(value, bool):
        value = str(value).lower()
    return f'<{kind} name={quoteattr(name)}>{escape(str(value))}</{kind}>'


def s(name, value):
    return _p("stringProp", name, value)


def b(name, value):
    return _p("boolProp", name, value)


def i(name, value):
    return _p("intProp", name, value)


def lp(name, value):
    return _p("longProp", name, value)


def el(tag: str, gui: str, name: str, props: list[str], children: list[str] | None = None, cls: str | None = None,
       enabled: bool = True) -> str:
    attrs = f'guiclass="{gui}" testclass="{cls or tag}" testname={quoteattr(name)}'
    if not enabled:
        attrs += ' enabled="false"'
    body = "".join(props)
    kids = "".join(children or [])
    return f"<{tag} {attrs}>{body}</{tag}><hashTree>{kids}</hashTree>"


def args(pairs: dict[str, str] | None = None, name="TestPlan.user_defined_variables") -> str:
    items = "".join(f'<elementProp name={quoteattr(k)} elementType="Argument">{s("Argument.name", k)}'
                    f'{s("Argument.value", v)}{s("Argument.metadata", "=")}</elementProp>'
                    for k, v in (pairs or {}).items())
    return (f'<elementProp name="{name}" elementType="Arguments" guiclass="ArgumentsPanel" testclass="Arguments" '
            f'testname="User Defined Variables"><collectionProp name="Arguments.arguments">{items}</collectionProp>'
            '</elementProp>')


def plan(name: str, children: list[str], comments: str = "", udv: dict | None = None, serialize=False) -> str:
    tp = (f'<TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname={quoteattr(name)}>'
          f'{s("TestPlan.comments", comments)}{b("TestPlan.functional_mode", False)}'
          f'{b("TestPlan.tearDown_on_shutdown", True)}{b("TestPlan.serialize_threadgroups", serialize)}'
          f'{args(udv)}</TestPlan>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">'
            f'<hashTree>{tp}<hashTree>{"".join(children)}</hashTree></hashTree></jmeterTestPlan>\n')


def defaults(domain="localhost", port="8080", protocol="http") -> str:
    return el("ConfigTestElement", "HttpDefaultsGui", "HTTP Request Defaults",
              [s("HTTPSampler.domain", domain), s("HTTPSampler.port", port), s("HTTPSampler.protocol", protocol),
               s("HTTPSampler.contentEncoding", "UTF-8"), args(name="HTTPsampler.Arguments")])


def headers(h: dict[str, str]) -> str:
    items = "".join(f'<elementProp name="" elementType="Header">{s("Header.name", k)}{s("Header.value", v)}'
                    '</elementProp>' for k, v in h.items())
    return el("HeaderManager", "HeaderPanel", "HTTP Header Manager",
              [f'<collectionProp name="HeaderManager.headers">{items}</collectionProp>'])


def cookies(clear_each_iteration=False) -> str:
    return el("CookieManager", "CookiePanel", "HTTP Cookie Manager",
              ['<collectionProp name="CookieManager.cookies"/>',
               b("CookieManager.clearEachIteration", clear_each_iteration),
               b("CookieManager.controlledByThreadGroup", False)])


def http(name: str, path: str, method="GET", body: str | None = None, children: list[str] | None = None,
         domain: str = "", port: str = "", enabled=True) -> str:
    if body is None:
        a = args(name="HTTPsampler.Arguments")
    else:
        a = ('<elementProp name="HTTPsampler.Arguments" elementType="Arguments"><collectionProp '
             'name="Arguments.arguments"><elementProp name="" elementType="HTTPArgument">'
             f'{b("HTTPArgument.always_encode", False)}{s("Argument.value", body)}{s("Argument.metadata", "=")}'
             '</elementProp></collectionProp></elementProp>')
    props = [s("HTTPSampler.domain", domain), s("HTTPSampler.port", port), s("HTTPSampler.path", path),
             b("HTTPSampler.follow_redirects", True), s("HTTPSampler.method", method),
             b("HTTPSampler.use_keepalive", True), b("HTTPSampler.postBodyRaw", body is not None), a]
    return el("HTTPSamplerProxy", "HttpTestSampleGui", name, props, children, enabled=enabled)


def status(code="200") -> str:
    return el("ResponseAssertion", "AssertionGui", f"Status {code}",
              [f'<collectionProp name="Asserion.test_strings">{s("49586", code)}</collectionProp>',
               s("Assertion.custom_message", ""), s("Assertion.test_field", "Assertion.response_code"),
               b("Assertion.assume_success", False), i("Assertion.test_type", 8)])


def think(ms: int, range_ms: int = 0) -> str:
    if range_ms:
        return el("UniformRandomTimer", "UniformRandomTimerGui", f"Think time {ms / 1000:g}-{(ms + range_ms) / 1000:g} s",
                  [s("ConstantTimer.delay", ms), s("RandomTimer.range", range_ms)])
    return el("ConstantTimer", "ConstantTimerGui", f"Think time {ms} ms", [s("ConstantTimer.delay", ms)])


def csv(path: str, name="Customers") -> str:
    return el("CSVDataSet", "TestBeanGUI", name,
              [s("filename", path), s("fileEncoding", "UTF-8"), s("variableNames", ""), b("ignoreFirstLine", False),
               s("delimiter", ","), b("quotedData", False), b("recycle", True), b("stopThread", False),
               s("shareMode", "shareMode.all")])


def transaction(name: str, children: list[str], parent=False) -> str:
    return el("TransactionController", "TransactionControllerGui", name,
              [b("TransactionController.includeTimers", False), b("TransactionController.parent", parent)], children)


def thread_group(name: str, threads, ramp, duration=None, children=(), delay=0, loops=-1,
                 setup=False, teardown=False) -> str:
    tag = "SetupThreadGroup" if setup else "PostThreadGroup" if teardown else "ThreadGroup"
    gui = "SetupThreadGroupGui" if setup else "PostThreadGroupGui" if teardown else "ThreadGroupGui"
    loop = ('<elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" '
            f'testclass="LoopController" testname="Loop Controller">{b("LoopController.continue_forever", False)}'
            f'{s("LoopController.loops", loops)}</elementProp>')
    props = [s("ThreadGroup.on_sample_error", "continue"), loop, s("ThreadGroup.num_threads", threads),
             s("ThreadGroup.ramp_time", ramp), b("ThreadGroup.scheduler", duration is not None),
             s("ThreadGroup.duration", duration if duration is not None else ""),
             s("ThreadGroup.delay", delay if duration is not None else ""),
             b("ThreadGroup.same_user_on_next_iteration", True)]
    return el(tag, gui, name, props, list(children))


def _jpgc(cls: str, name: str, props: list[str], children) -> str:
    return el(cls, cls + "Gui", name, props, list(children))


def concurrency(name: str, target, ramp, hold, steps=0, children=(), unit="S") -> str:
    cls = "com.blazemeter.jmeter.threads.concurrency.ConcurrencyThreadGroup"
    return _jpgc(cls, name, [
        '<elementProp name="ThreadGroup.main_controller" elementType="com.blazemeter.jmeter.control.VirtualUserController"/>',
        s("ThreadGroup.on_sample_error", "continue"), s("TargetLevel", target), s("RampUp", ramp),
        s("Steps", steps or ""), s("Hold", hold), s("LogFilename", ""), s("Iterations", ""), s("Unit", unit)], children)


def arrivals(name: str, rate, ramp, hold, steps=0, limit="", children=(), unit="S") -> str:
    cls = "com.blazemeter.jmeter.threads.arrivals.ArrivalsThreadGroup"
    return _jpgc(cls, name, [
        '<elementProp name="ThreadGroup.main_controller" elementType="com.blazemeter.jmeter.control.VirtualUserController"/>',
        s("ThreadGroup.on_sample_error", "continue"), s("TargetLevel", rate), s("RampUp", ramp),
        s("Steps", steps or ""), s("Hold", hold), s("LogFilename", ""), s("Iterations", ""),
        s("ConcurrencyLimit", limit), s("Unit", unit)], children)


def _table(name: str, rows) -> str:
    out = "".join(f'<collectionProp name="{k}">' + "".join(s(str(j + 1), v) for j, v in enumerate(r))
                  + "</collectionProp>" for k, r in enumerate(rows))
    return f'<collectionProp name="{name}">{out}</collectionProp>'


def ultimate(name: str, rows, children=()) -> str:
    """rows: (threads, initial delay s, startup s, hold s, shutdown s)"""
    cls = "kg.apc.jmeter.threads.UltimateThreadGroup"
    loop = ('<elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" '
            f'testclass="LoopController" testname="Loop Controller">{b("LoopController.continue_forever", False)}'
            f'{i("LoopController.loops", -1)}</elementProp>')
    return _jpgc(cls, name, [_table("ultimatethreadgroupdata", rows), loop,
                             s("ThreadGroup.on_sample_error", "continue")], children)


def freeform(name: str, rows, limit="", children=()) -> str:
    """rows: (start rate, end rate, duration s)"""
    cls = "com.blazemeter.jmeter.threads.arrivals.FreeFormArrivalsThreadGroup"
    return _jpgc(cls, name, [
        '<elementProp name="ThreadGroup.main_controller" elementType="com.blazemeter.jmeter.control.VirtualUserController"/>',
        s("ThreadGroup.on_sample_error", "continue"), s("LogFilename", ""), s("Iterations", ""),
        s("ConcurrencyLimit", limit), s("Unit", "S"), _table("Schedule", rows)], children)


def open_model(name: str, schedule: str, children=()) -> str:
    return el("OpenModelThreadGroup", "OpenModelThreadGroupGui", name,
              [s("OpenModelThreadGroup.schedule", schedule), s("OpenModelThreadGroup.random_seed", "")], list(children))


def constant_throughput(per_minute: float, mode: int = 1) -> str:
    return el("ConstantThroughputTimer", "TestBeanGUI", f"{per_minute / 60:g} req/s",
              [i("calcMode", mode), f"<doubleProp><name>throughput</name><value>{float(per_minute)}</value>"
                                    "<savedValue>0.0</savedValue></doubleProp>"])


def shaping_timer(rows) -> str:
    """rows: (start req/s, end req/s, duration s)"""
    cls = "kg.apc.jmeter.timers.VariableThroughputTimer"
    return el(cls, cls + "Gui", "jp@gc - Throughput Shaping Timer", [_table("load_profile", rows)])


def listener(name="View Results Tree") -> str:
    return el("ResultCollector", "ViewResultsFullVisualizer", name,
              [b("ResultCollector.error_logging", False), s("filename", "")])


def jsr223_timer(name="Pacing", script="return 0", parameters="") -> str:
    return el("JSR223Timer", "TestBeanGUI", name,
              [s("scriptLanguage", "groovy"), s("parameters", parameters), s("filename", ""), s("cacheKey", "true"),
               s("script", script)])


def plugin_element(cls: str, name: str = "Plugin element") -> str:
    return el(cls, cls + "Gui", name, [])
