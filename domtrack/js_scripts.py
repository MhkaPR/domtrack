"""Browser-side capture scripts.

The mutation stream is serialised in an `rrweb`-style shape: a single
*full snapshot* of the DOM (every node gets a numeric id) followed by
*incremental* records that reference nodes by id and only describe what
actually changed.

See ``domtrack.trace`` for the Python-side format definition, and
``docs/trace-format.md`` for the full specification.

Notes on the ``%`` formatting used below: the templates are raw strings
containing a lot of literal ``{``/``}`` (JavaScript), so we use
``%``-formatting instead of ``str.format``/f-strings.
"""

from __future__ import annotations

import json

from .config import TracerConfig


# ── rrweb record / node constants (mirrored in domtrack/trace.py) ──────────
# RecordType
RT_INCREMENTAL_SNAPSHOT = 3

# IncrementalSource
SOURCE_MUTATION = 0

# NodeType
NT_DOCUMENT = 0
NT_DOCUMENT_TYPE = 1
NT_ELEMENT = 2
NT_TEXT = 3
NT_COMMENT = 5


HELPERS_INIT = r"""
(dynamicClasses) => {
    const DYN = new Set(dynamicClasses);
    window.__cssPath = function(el) {
        if (!el || el.nodeType !== 1) return null;
        if (el.id) return '#' + CSS.escape(el.id);
        const parts = [];
        let curr = el;
        while (curr && curr.nodeType === 1 && curr !== document.documentElement) {
            let part = curr.tagName.toLowerCase();
            if (curr.classList && curr.classList.length) {
                const stable = [...curr.classList]
                    .filter(c => !DYN.has(c)).slice(0, 3);
                if (stable.length) part += '.' + stable.map(CSS.escape).join('.');
            }
            const parent = curr.parentElement;
            if (parent) {
                const same = [...parent.children].filter(c => c.tagName === curr.tagName);
                if (same.length > 1) part += `:nth-of-type(${same.indexOf(curr) + 1})`;
            }
            parts.unshift(part);
            curr = parent;
        }
        return parts.join(' > ');
    };
}
"""


# The mutation recorder. Installs a MutationObserver that emits rrweb-style
# incremental records (source = 0) plus a full snapshot of the initial DOM.
INIT_TEMPLATE = r"""
(targetSelector) => {
    const MAX_TEXT = %(max_text)d;
    const MAX_NODES = %(max_nodes)d;
    const MAX_SNAPSHOT_DEPTH = %(snapshot_max_depth)d;
    const MAX_SNAPSHOT_NODES = %(snapshot_max_nodes)d;
    const DYNAMIC_CLASSES = new Set(%(dynamic_classes)s);

    // ── node identity ────────────────────────────────────────────────────
    // Every node seen by the recorder gets a stable numeric id. Nodes that
    // were part of the initial snapshot already have one; nodes created
    // later are assigned lazily the first time a record references them.
    window.__domtrack = {
        nextId: 1,
        idOf: new WeakMap(),   // Node -> numeric id
        nodes: new Map(),      // numeric id -> serialized node
    };

    function idFor(node) {
        let id = window.__domtrack.idOf.get(node);
        if (id === undefined) {
            id = window.__domtrack.nextId++;
            window.__domtrack.idOf.set(node, id);
        }
        return id;
    }

    // ── selector helper (mirrors window.__cssPath) ───────────────────────
    function cssPath(el) {
        if (!el || el.nodeType !== 1) return null;
        if (el.id) return '#' + CSS.escape(el.id);
        const parts = [];
        let curr = el;
        while (curr && curr.nodeType === 1 && curr !== document.documentElement) {
            let part = curr.tagName.toLowerCase();
            if (curr.classList && curr.classList.length) {
                const stable = [...curr.classList]
                    .filter(c => !DYNAMIC_CLASSES.has(c))
                    .slice(0, 3);
                if (stable.length) part += '.' + stable.map(CSS.escape).join('.');
            }
            const parent = curr.parentElement;
            if (parent) {
                const same = [...parent.children].filter(c => c.tagName === curr.tagName);
                if (same.length > 1) part += `:nth-of-type(${same.indexOf(curr) + 1})`;
            }
            parts.unshift(part);
            curr = parent;
        }
        return parts.join(' > ');
    }

    // ── compact node reference used inside records ───────────────────────
    // Deliberately tiny: no text, no html, no rect. Replay only needs
    // tag / id / selector to locate the element again.
    function nodeRef(el) {
        if (!el || el.nodeType !== 1) return null;
        return {
            nodeId: idFor(el),
            tag: el.tagName,
            id: el.id || null,
            selector: cssPath(el),
        };
    }

    // ── full snapshot serialization (rrweb FullSnapshot node) ────────────
    function serializeNode(node, depth) {
        const id = idFor(node);
        const nt = node.nodeType;
        let sn;
        if (nt === 3) {
            sn = { id, type: 3, textContent: (node.nodeValue || '').slice(0, MAX_TEXT) };
        } else if (nt === 8) {
            sn = { id, type: 5, textContent: (node.nodeValue || '').slice(0, MAX_TEXT) };
        } else if (nt === 10) {
            sn = { id, type: 1, name: node.name, publicId: node.publicId, systemId: node.systemId };
        } else if (nt !== 1) {
            sn = { id, type: 2, tagName: '#' + node.nodeName };
        } else {
            const attributes = {};
            for (const a of node.attributes || []) attributes[a.name] = a.value;
            sn = { id, type: 2, tagName: node.tagName, attributes: attributes, childNodes: [] };
            // Remember every serialized node so later records can reference
            // it by id, and so replay can rebuild its HTML.
            window.__domtrack.nodes.set(id, sn);
            if (depth < MAX_SNAPSHOT_DEPTH && window.__domtrack.nodes.size < MAX_SNAPSHOT_NODES) {
                for (const child of node.childNodes) {
                    sn.childNodes.push(serializeNode(child, depth + 1));
                }
            }
            return sn;
        }
        window.__domtrack.nodes.set(id, sn);
        return sn;
    }

    window.__domtrack.snapshot = {
        node: {
            id: idFor(document),
            type: 0,
            childNodes: Array.from(document.childNodes)
                .filter(n => n.nodeType === 1 || n.nodeType === 10)
                .map(n => serializeNode(n, 0)),
        },
        initialOffset: { top: window.scrollY || 0, left: window.scrollX || 0 },
    };

    // ── incremental recording ────────────────────────────────────────────
    window.capturedMutations = [];
    window.__traceStartTime = performance.now();

    function siblingId(node) {
        return node && node.nodeType === 1 ? idFor(node) : null;
    }

    window.scrapeObserver = new MutationObserver((records) => {
        const now = performance.now() - window.__traceStartTime;
        for (const r of records) {
            const data = { source: 0, target: nodeRef(r.target) };
            let type;

            if (r.type === 'attributes') {
                type = 'attributes';
                data.attributes = [{
                    id: idFor(r.target),
                    name: r.attributeName,
                    namespace: r.attributeNamespace,
                    oldValue: r.oldValue,
                    value: r.target.getAttribute
                        ? r.target.getAttribute(r.attributeName) : null,
                }];
            } else if (r.type === 'characterData') {
                type = 'characterData';
                data.texts = [{
                    id: idFor(r.target),
                    oldValue: r.oldValue,
                    value: r.target.nodeValue,
                }];
            } else if (r.type === 'childList') {
                type = 'childList';
                const parentId = idFor(r.target);
                const nextId = siblingId(r.nextSibling);
                data.adds = [];
                data.removes = [];

                for (const n of r.addedNodes) {
                    if (!n || (n.nodeType !== 1 && n.nodeType !== 3 && n.nodeType !== 8)) continue;
                    if (data.adds.length >= MAX_NODES) break;
                    if (window.__domtrack.idOf.has(n)) {
                        // already serialized earlier — reference it by id only
                        data.adds.push({ parentId: parentId, nextId: nextId, node: { id: idFor(n) } });
                    } else {
                        data.adds.push({ parentId: parentId, nextId: nextId, node: serializeNode(n, 0) });
                    }
                }

                for (const n of r.removedNodes) {
                    if (!n) continue;
                    if (data.removes.length >= MAX_NODES) break;
                    const ref = { parentId: parentId, nodeId: idFor(n), isParent: false };
                    if (n.nodeType === 1) {
                        ref.tag = n.tagName;
                        ref.id = n.id || null;
                        ref.selector = cssPath(n);
                    }
                    data.removes.push(ref);
                }

                data.addedCount = r.addedNodes.length;
                data.removedCount = r.removedNodes.length;
            } else {
                continue;
            }

            // `type` mirrors the rrweb record `type`; `data.source` mirrors
            // `IncrementalSource.Mutation`. `data.target` is a domtrack
            // extension carrying the compact node reference replay needs.
            data.type = type;
            window.capturedMutations.push({
                t: Math.round(now),
                type: %(rt_incremental)d,
                data: data,
            });
        }
    });

    const target = targetSelector
        ? document.querySelector(targetSelector)
        : document.body;
    if (!target) throw new Error("Target not found: " + targetSelector);

    window.scrapeObserver.observe(target, {
        childList: true, subtree: true,
        attributes: true, attributeOldValue: true,
        characterData: true, characterDataOldValue: true,
    });
}
"""


COLLECT_SCRIPT = """
() => {
    if (window.scrapeObserver) window.scrapeObserver.disconnect();
    const d = window.__domtrack || {};
    return {
        meta: {
            url: location.href,
            title: document.title,
            ua: navigator.userAgent,
            viewport: { w: innerWidth, h: innerHeight },
            capturedAt: new Date().toISOString(),
        },
        count: window.capturedMutations.length,
        snapshot: d.snapshot || null,
        records: window.capturedMutations,
        events: window.__events || [],
    };
}
"""


EVENT_INIT = r"""
(config) => {
    window.__events = [];
    const t0 = performance.now();

    const targetInfo = (el) => {
        if (!el || el.nodeType !== 1) return null;
        return {
            tag: el.tagName,
            id: el.id || null,
            selector: window.__cssPath(el),
            text: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80),
        };
    };

    const push = (kind, e) => {
        try {
            const entry = { t: Math.round(performance.now() - t0), kind };
            const el = e.target;
            if (el && el.nodeType === 1) {
                try {
                    entry.target = targetInfo(el);
                } catch (_) {
                    entry.target = null;
                }
            }
            if ('clientX' in e) {
                entry.clientX = Math.round(e.clientX);
                entry.clientY = Math.round(e.clientY);
            }
            entry.modifiers = {
                ctrl: !!e.ctrlKey, shift: !!e.shiftKey,
                alt: !!e.altKey,  meta: !!e.metaKey,
            };
            if (kind === 'keydown' || kind === 'keyup') {
                entry.key = e.key; entry.code = e.code; entry.repeat = !!e.repeat;
            }
            if (kind === 'input' || kind === 'change') {
                entry.value = (el && el.value !== undefined) ? el.value : null;
            }
            if (kind === 'mousedown' || kind === 'mouseup' || kind === 'click'
                || kind === 'dblclick' || kind === 'contextmenu') {
                entry.button = e.button; entry.buttons = e.buttons;
            }
            if (kind === 'wheel') { entry.deltaX = e.deltaX; entry.deltaY = e.deltaY; }
            window.__events.push(entry);
        } catch (_) {
            // never let a listener exception silently drop a real event
        }
    };

    // NOTE: 'mousemove' is deliberately NOT captured — it produces the
    // most entries per second with the least semantic value, and Playwright
    // synthesizes its own mousemove for every click coordinate anyway.
    const kinds = [
        'click', 'dblclick', 'contextmenu',
        'mousedown', 'mouseup',
        'pointerdown', 'pointerup',
        'keydown', 'keyup',
        'input', 'change', 'submit', 'focus', 'blur',
        'wheel',
    ];
    for (const k of kinds) {
        document.addEventListener(k, (e) => push(k, e), { capture: true, passive: true });
    }

    // Scroll — throttled
    let lastScroll = 0;
    document.addEventListener('scroll', (e) => {
        const now = performance.now();
        if (now - lastScroll < config.scrollThrottleMs) return;
        lastScroll = now;
        const el = e.target === document ? document.documentElement : e.target;
        const docScroll = el === document.documentElement;
        window.__events.push({
            t: Math.round(performance.now() - t0),
            kind: 'scroll',
            target: docScroll ? null : targetInfo(el),
            scrollX: docScroll ? window.scrollX : el.scrollLeft,
            scrollY: docScroll ? window.scrollY : el.scrollTop,
            isDocument: docScroll,
        });
    }, { capture: true, passive: true });
}
"""


def build_init_script(cfg: TracerConfig) -> str:
    """Render the mutation-recorder init script for ``cfg``."""
    return INIT_TEMPLATE % {
        "max_text": cfg.max_text,
        "max_nodes": cfg.max_nodes,
        "snapshot_max_depth": cfg.snapshot_max_depth,
        "snapshot_max_nodes": cfg.snapshot_max_nodes,
        "dynamic_classes": json.dumps(list(cfg.dynamic_classes)),
        "rt_incremental": RT_INCREMENTAL_SNAPSHOT,
    }
