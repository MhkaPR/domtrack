import json
import os
import csv
from datetime import datetime
from playwright.sync_api import sync_playwright

TARGET_URL = "https://www.iranserver.com/web-hosting/wordpress/"
TARGET_SELECTOR = None  # None = کل body

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=360000)

    page.evaluate("""
    (targetSelector) => {
        window.capturedMutations = [];
        window.__traceStartTime = performance.now();

        const MAX_TEXT = 500;
        const MAX_HTML = 2000;
        const MAX_NODES = 50;
        const DYNAMIC_CLASSES = /^(d-none|d-tablecell|d-block|active|addctived|show|hidden|fade|collapse|collapsing|open|closed)$/;

        // ───── مسیر پایدار CSS برای هر عنصر ─────
        function cssPath(el) {
            if (!el || el.nodeType !== 1) return null;
            if (el.id) return '#' + CSS.escape(el.id);
            const parts = [];
            let curr = el;
            while (curr && curr.nodeType === 1 && curr !== document.documentElement) {
                let part = curr.tagName.toLowerCase();
                if (curr.classList && curr.classList.length) {
                    const stable = [...curr.classList]
                        .filter(c => !DYNAMIC_CLASSES.test(c))
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

        // ───── اطلاعات کامل یک گره ─────
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
                    text: (node.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, MAX_TEXT),
                    html: (node.outerHTML || '').slice(0, MAX_HTML),
                    attributes: attrs,
                };
            }
            return { nodeType: node.nodeType, name: node.nodeName };
        }

        // ───── اطلاعات عنصر هدف (target) ─────
        function targetInfo(el) {
            if (!el || el.nodeType !== 1) return null;
            const r = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
            return {
                tag: el.tagName,
                id: el.id || null,
                class: el.className || null,
                selector: cssPath(el),
                text: (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, MAX_TEXT),
                parentSelector: el.parentElement ? cssPath(el.parentElement) : null,
                rect: r ? { x: Math.round(r.x), y: Math.round(r.y),
                             w: Math.round(r.width), h: Math.round(r.height) } : null,
            };
        }

        // ───── Observer ─────
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

        const target = targetSelector ? document.querySelector(targetSelector) : document.body;
        if (!target) throw new Error("Target not found: " + targetSelector);

        window.scrapeObserver.observe(target, {
            childList: true, subtree: true,
            attributes: true, attributeOldValue: true,
            characterData: true, characterDataOldValue: true,
        });
    }
    """, TARGET_SELECTOR)

    print("⏺ trace started — page.pause() باز شد.")
    print("   هر اکشنی خواستی انجام بده، بعد دکمه Resume را بزن.")

    page.pause()

    # ───── جمع‌آوری داده‌ها ─────
    payload = page.evaluate("""
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
        };
    }
    """)

    out_dir = "traces"
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = os.path.join(out_dir, f"trace_{stamp}.json")
    csv_path  = os.path.join(out_dir, f"trace_{stamp}.csv")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
