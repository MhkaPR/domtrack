# Examples

Copy-paste recipes. All assume `pip install -e .` and
`playwright install chromium`.

## 1. Record and replay, end to end

```python
from domtrack import TracerConfig
from domtrack.capture import Recorder
from domtrack.replay import Replayer, ReplayConfig

cfg = TracerConfig(url="https://example.com", headless=True, duration_ms=10_000)
result = Recorder(cfg).run()
print(result.json_path, result.mutation_count, result.event_count)

replayer = Replayer.from_file(result.json_path, config=ReplayConfig(speed=2.0))
replayer.run()
replayer.print_report()
```

## 2. Record only a subtree

```python
from domtrack import TracerConfig
from domtrack.capture import Recorder

cfg = TracerConfig(url="https://example.com", target_selector="main",
                   headless=True, duration_ms=8000)
Recorder(cfg).run()
```

## 3. Replay forced DOM patches (exact state reproduction)

```python
from domtrack.replay import Replayer, ReplayConfig

cfg = ReplayConfig(replay_mode="mutations", time_policy="instant", headless=True)
Replayer.from_file("traces/trace_x.json", config=cfg).run()
```

## 4. Skip third-party widgets and noisy events

```python
from domtrack.replay import Replayer, ReplayConfig

cfg = ReplayConfig(
    mode="clean",                             # ThirdPartyFilter on
    replay_mode="events",
    skip_events=("wheel", "scroll"),          # ignore these kinds
    time_policy="skip-aware",
    gap_cap_ms=250,
)
Replayer.from_file("traces/trace_x.json", config=cfg).run()
```

## 5. Inspect a trace without a browser

```python
from domtrack import trace

raw = trace.load_raw("traces/trace_x.json")
print("rrweb-style:", trace.is_rrweb_trace(raw))
index = trace.index_snapshot(raw["snapshot"]["node"])
print("nodes in snapshot:", len(index))

muts = trace.to_mutation_dicts(raw)
from collections import Counter
print(Counter(m["type"] for m in muts))

# Rebuild the HTML of a node by id:
print(trace.node_to_html(index[4]))
```

## 6. Custom time policy

```python
from domtrack.replay.event_replayer import EventReplayer
from domtrack.replay.timing import TimePolicy, TimeContext

class HalfSpeed(TimePolicy):
    def compute_wait(self, ctx: TimeContext) -> int:
        return max(0, int((ctx.curr_t - ctx.prev_t) * 2))

# use it directly:
replayer = EventReplayer(events, HalfSpeed())
```

## 7. Custom mutation filter

```python
from domtrack.replay.filters import CompositeFilter, MutationFilter, FilterResult

class SkipAds(MutationFilter):
    def check(self, mutation):
        sel = mutation.target.selector or ""
        if "ad-slot" in sel:
            return FilterResult.block("ad slot")
        return FilterResult.allow()

composite = CompositeFilter([SkipAds()])
```

## 8. Detect third-party widgets with custom hints

```python
from domtrack.replay import Replayer, ReplayConfig, ThirdPartyDetector

detector = ThirdPartyDetector(hints=["my-chat", "flowchat", "goftino"])
Replayer.from_file("traces/trace_x.json",
                   config=ReplayConfig(mode="clean"),
                   third_party_detector=detector).run()
```

## 9. Load a legacy 1.x trace

```python
from domtrack.replay import Trace

loaded = Trace.load("traces/trace_limoo.json")  # flat "mutations" layout
print(len(loaded.mutations), loaded.meta.url)   # works unchanged
```

## 10. Write your own JSON from a raw payload

```python
from domtrack import TracerConfig
from domtrack.capture import Recorder, save_json, save_csv

recorder = Recorder(TracerConfig(url="https://example.com",
                                 headless=True, duration_ms=5000))
payload = recorder._build_payload(recorder.capture())
save_json(payload, "traces", stamp="custom")
save_csv(payload["records"], "traces", stamp="custom")
```
