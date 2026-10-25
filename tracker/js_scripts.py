from __future__ import annotations

import json

from .config import TracerConfig


# NOTE: raw string so that \s inside the JS regex stays intact.
#       %-formatting is used (not .format / f-string) because the JS is
#       full of { } that must not be interpreted as format fields.
INIT_TEMPLATE = r"""
(targetSelector) => {
    const MAX_TEXT = %(max_text)d;
    const MAX_HTML = %(max_html)d;
    const MAX_NODES = %(max_nodes)d;
    const DYNAMIC_CLASSES = new Set(%(dynamic_classes)s);

    window.capturedMutations = [];
    window.__traceStartTime = performance.now();

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

    function nodeInfo(node) {
        if (!node) return null;
        if (node.nodeType === Node.TEXT_NODE) {
            return { nodeType: 'text', value: (node.nodeValue || '').slice(0, MAX_TEXT) };
        }
        if (node.nodeType === Node.ELEMENT_NODE) {
            const attrs = {};
            for (const a of node.attributes || []) attrs[a.name] = a.value;
            return {
                nodeType: 'element',
                tag: node.tagName,
                id: node.id || null,
                class: node.className || null,
                selector: cssPath(node),
                text: (node.textContent || '').replace(/\s+/g, ' ').trim().slice(0, MAX_TEXT),
                html: (node.outerHTML || '').slice(0, MAX_HTML),
                attributes: attrs,
            };
        }
        return { nodeType: node.nodeType, name: node.nodeName };
    }

    function targetInfo(el) {
        if (!el || el.nodeType !== 1) return null;
        const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
        return {
            tag: el.tagName,
            id: el.id || null,
            class: el.className || null,
            selector: cssPath(el),
            text: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, MAX_TEXT),
            parentSelector: el.parentElement ? cssPath(el.parentElement) : null,
            rect: r ? {
                x: Math.round(r.x), y: Math.round(r.y),
                w: Math.round(r.width), h: Math.round(r.height)
            } : null,
        };
    }

    window.scrapeObserver = new MutationObserver((records) => {
        const now = performance.now() - window.__traceStartTime;
        for (const r of records) {
            const entry = {
                t: Math.round(now),
                type: r.type,
                target: targetInfo(r.target),
            };

            if (r.type === 'attributes') {
                entry.attributeName = r.attributeName;
                entry.attributeNamespace = r.attributeNamespace;
                entry.oldValue = r.oldValue;
                entry.newValue = r.target.getAttribute
                    ? r.target.getAttribute(r.attributeName) : null;
            }

            if (r.type === 'characterData') {
                entry.oldValue = r.oldValue;
                entry.newValue = r.target.nodeValue;
            }

            if (r.type === 'childList') {
                entry.addedCount = r.addedNodes.length;
                entry.removedCount = r.removedNodes.length;
                entry.added = Array.from(r.addedNodes).slice(0, MAX_NODES).map(nodeInfo);
                entry.removed = Array.from(r.removedNodes).slice(0, MAX_NODES).map(nodeInfo);
                entry.previousSibling = nodeInfo(r.previousSibling);
                entry.nextSibling = nodeInfo(r.nextSibling);
            }

            window.capturedMutations.push(entry);
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
    return {
        meta: {
            url: location.href,
            title: document.title,
            ua: navigator.userAgent,
            viewport: { w: innerWidth, h: innerHeight },
            capturedAt: new Date().toISOString(),
        },
        count: window.capturedMutations.length,
        mutations: window.capturedMutations,
        events: window.__events || [],
    };
}
"""


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
        const entry = { t: Math.round(performance.now() - t0), kind };
        const el = e.target;
        if (el && el.nodeType === 1) entry.target = targetInfo(el);
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
    };

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

    // Mousemove — throttled to ~25 Hz
    let lastMove = 0;
    document.addEventListener('mousemove', (e) => {
        const now = performance.now();
        if (now - lastMove < config.moveThrottleMs) return;
        lastMove = now;
        push('mousemove', e);
    }, { capture: true, passive: true });

    // Scroll — throttled to ~10 Hz (captures bubbling element scrolls too)
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
    return INIT_TEMPLATE % {
        "max_text": cfg.max_text,
        "max_html": cfg.max_html,
        "max_nodes": cfg.max_nodes,
        "dynamic_classes": json.dumps(list(cfg.dynamic_classes)),
    }