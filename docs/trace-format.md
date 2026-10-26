# Trace format

This document is the authoritative description of the `domtrack` trace file.
It is modelled on [`rrweb`](https://rrweb.com/docs/): a single full snapshot
followed by incremental records.

Everything here is produced by `domtrack.js_scripts` (capture) and consumed by
`domtrack.trace` + `domtrack.replay.models` (replay). The constants used below
are mirrored in Python as `domtrack.trace.RecordType`,
`domtrack.trace.IncrementalSource`, and `domtrack.trace.NodeType`.

## Top-level document

```jsonc
{
  "meta":     { /* Meta */ },
  "version":  2,
  "count":    334,                 // number of entries in `records`
  "snapshot": { /* FullSnapshot */ },
  "records":  [ /* Record... */ ],
  "events":   [ /* Event... */ ]
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `meta` | `Meta` | Page metadata captured at collect time |
| `version` | `int` | `2` for the rrweb-style layout |
| `count` | `int` | Length of `records` |
| `snapshot` | `FullSnapshot` or `null` | The serialized initial DOM |
| `records` | `Record[]` | Incremental mutation records |
| `events` | `Event[]` | Captured user interactions |

### Meta

```jsonc
{
  "url": "https://example.com/page",
  "title": "Page title",
  "ua": "Mozilla/5.0 ...",
  "viewport": { "w": 1440, "h": 900 },
  "capturedAt": "2026-01-01T12:00:00.000Z"
}
```

`viewport` accepts `w`/`h` (capture) or `width`/`height` (both are read on
load).

## FullSnapshot

```jsonc
{
  "type": 2,                 // RecordType.FULL_SNAPSHOT
  "node": { /* SerializedNode (document) */ },
  "initialOffset": { "top": 0, "left": 0 }
}
```

### SerializedNode

Every node has a numeric `id` (unique within the trace) and a `type`:

| `type` | Meaning | Relevant fields |
| --- | --- | --- |
| `0` | Document | `childNodes` |
| `1` | DocumentType | `name`, `publicId`, `systemId` |
| `2` | Element | `tagName`, `attributes`, `childNodes` |
| `3` | Text | `textContent` |
| `5` | Comment | `textContent` |

Example element node:

```jsonc
{
  "id": 4,
  "type": 2,
  "tagName": "DIV",
  "attributes": { "id": "box", "class": "a b" },
  "childNodes": [
    { "id": 5, "type": 3, "textContent": "hello" }
  ]
}
```

The snapshot root is the document node (`type: 0`); its `childNodes` are the
`<html>` element (and any doctype), filtered to element/doctype nodes at the
top level.

Snapshot limits (from `TracerConfig`): `snapshot_max_depth` (default 20) and
`snapshot_max_nodes` (default 20000). Nodes beyond the limit are emitted
without children so the tree stays valid.

## Incremental records

Each record is a mutation event at time `t` (milliseconds since capture start):

```jsonc
{ "t": 350, "type": 3, "data": { "source": 0, "type": "attributes" } }
```

| Field | Type | Notes |
| --- | --- | --- |
| `t` | `int` | ms since the recorder started |
| `type` | `int` | Always `3` (`RecordType.INCREMENTAL_SNAPSHOT`) |
| `data` | `object` | See below |

`data.source` is always `0` (`IncrementalSource.MUTATION`). `data.type` is one
of `"attributes"`, `"characterData"`, `"childList"`.

`data.target` is a **node reference** (domtrack extension) used to re-locate
the affected element during replay:

```jsonc
{ "nodeId": 4, "tag": "DIV", "id": "box", "selector": "#box" }
```

It is `null` for text-node targets (characterData on a text node has no
element identity). This is why records never embed the target's text or HTML:
replay needs only enough to find the node again.

### attributes

```jsonc
{
  "source": 0,
  "type": "attributes",
  "target": { "nodeId": 4, "tag": "DIV", "id": "box", "selector": "#box" },
  "attributes": [
    { "id": 4, "name": "class", "namespace": null, "oldValue": "a b", "value": "a c" }
  ]
}
```

### characterData

```jsonc
{
  "source": 0,
  "type": "characterData",
  "target": null,
  "texts": [ { "id": 5, "oldValue": "hello", "value": "world" } ]
}
```

### childList

```jsonc
{
  "source": 0,
  "type": "childList",
  "target": { "nodeId": 4, "tag": "DIV", "id": "box", "selector": "#box" },
  "addedCount": 1,
  "removedCount": 1,
  "adds": [
    {
      "parentId": 4,
      "nextId": null,
      "node": { "id": 6, "type": 2, "tagName": "SPAN",
                "attributes": { "id": "new" }, "childNodes": [] }
    }
  ],
  "removes": [
    { "parentId": 4, "nodeId": 5, "isParent": false,
      "tag": "P", "id": null, "selector": "p" }
  ]
}
```

* `adds[].node` is a full `SerializedNode` for a newly created element, or a
  bare `{ "id": n }` reference when the node was already serialized earlier.
* `adds[].parentId` / `adds[].nextId` give insertion position.
* `removes[]` reference the removed node by `nodeId`, plus a compact
  `tag`/`id`/`selector` reference (when it was an element) so replay can remove
  it without the original HTML.
* `addedCount` / `removedCount` are the raw observer counts (may exceed the
  number of captured entries when capped by `max_nodes`).

## Events

User interactions captured alongside mutations. Each event has `t`, `kind`,
an optional `target`, and kind-specific fields:

```jsonc
{ "t": 120, "kind": "click", "target": { "tag": "A", "id": null,
  "selector": "body > a.buy", "text": "Buy" }, "clientX": 300, "clientY": 400,
  "button": 0, "buttons": 1, "modifiers": { "ctrl": false, "shift": false,
  "alt": false, "meta": false } }
```

Kinds: `click`, `dblclick`, `contextmenu`, `mousedown`, `mouseup`,
`pointerdown`, `pointerup`, `keydown`, `keyup`, `input`, `change`, `submit`,
`focus`, `blur`, `wheel`, `scroll`. (`mousemove` is intentionally excluded.)

Kind-specific fields: keyboard -> `key`, `code`, `repeat`; input/change ->
`value`; pointer buttons -> `button`, `buttons`; wheel -> `deltaX`, `deltaY`;
scroll -> `scrollX`, `scrollY`, `isDocument`.

## Conversion to replay mutations

`domtrack.trace.to_mutation_dicts(raw)` turns a trace into the flat dicts that
`domtrack.replay.models.Mutation.from_dict` expects:

* `attributes` record -> `{type: "attributes", attributeName, oldValue, newValue}`
* `characterData` record -> `{type: "characterData", oldValue, newValue}`
* `childList` record -> `{type: "childList", added: [NodeInfo], removed: [NodeInfo]}`

`NodeInfo` is `{id, selector, html}`; `html` is reconstructed from the snapshot
index via `domtrack.trace.node_to_html`.

## Rebuilding HTML at a record point

`domtrack.trace` can also fold a trace back into a live DOM tree with no
browser. `DomState` starts from the `FullSnapshot` and applies incremental
records in order, so you can serialize the HTML *as it existed at any point*:

```python
from domtrack import trace

raw = trace.load_raw("traces/trace_x.json")

state = trace.DomState.from_trace(raw)     # seed from the FullSnapshot
state.apply_up_to(raw["records"], at_ms=1200)  # stop at t <= 1200ms
html = state.to_html()

# one-call shortcut:
html_at_1200 = trace.rebuild_html(raw, at_ms=1200)
html_final   = trace.rebuild_html(raw)     # all records
```

`DomState` handles all three mutation kinds (`attributes`, `characterData`,
`childList`) and keeps two guarantees that make it safe to fold the same
stream repeatedly:

* **no duplicates** — an `add` whose node already sits under the target parent
  is skipped (counted in `stats["skipped_duplicates"]`); a node moved from
  another parent is detached first.
* **removals unregister** — when `removes[].isParent` is set, the node and its
  descendants are dropped from the id index too.

`state.stats` reports `attributes`, `text`, `adds`, `removes`,
`skipped_duplicates`, and `missing_targets`. `state.mirror` maps live node ids
to their reconstructed nodes, and `state.clock` is the timestamp of the last
applied record.

`render_node` is the shared serializer behind both `node_to_html` and
`DomState.to_html`; unlike `node_to_html` it also renders a document root
(`type 0`) by concatenating its children.

## Legacy format (domtrack 1.x)

Older traces have no `snapshot`/`records`; they have a flat array:

```jsonc
{ "meta": {}, "count": 334, "mutations": [ { "t": 0, "type": "attributes",
  "target": { "tag": "DIV", "id": null, "class": "x", "selector": "...",
  "text": "...", "html": "...", "parentSelector": "...", "rect": {} } } ] }
```

`domtrack.trace.is_rrweb_trace(raw)` returns `False` for these and
`to_mutation_dicts` returns the `mutations` array as-is, so old files keep
replaying unchanged.
