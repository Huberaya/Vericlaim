"""Dynamic JavaScript Rendering & Client-Side Hydration Extractor for E-Commerce.

Handles modern dynamic Single Page Applications (Next.js Commerce, Shopify Hydrogen,
Nuxt, Vue Storefront, React SPAs) by extracting structured state, JSON-LD,
hydration stores, and executing client-side DOM mutations in a sandboxed runner.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from typing import Any

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def extract_json_ld_product(soup: BeautifulSoup) -> list[str]:
    """Extract product metadata from Schema.org <script type="application/ld+json">."""
    sections: list[str] = []
    for tag in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.IGNORECASE)}):
        if not tag.string:
            continue
        try:
            data = json.loads(tag.string)
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                # Handle Graph or nested product
                graph = item.get("@graph")
                sub_items = graph if isinstance(graph, list) else [item]
                for sub in sub_items:
                    if not isinstance(sub, dict):
                        continue
                    item_type = str(sub.get("@type", "")).lower()
                    if "product" in item_type or "itempage" in item_type:
                        name = sub.get("name") or sub.get("headline")
                        desc = sub.get("description")
                        if name:
                            sections.append(f"Titre Produit (Données structurées) : {name}")
                        if desc:
                            sections.append(f"Description Produit (Données structurées) : {desc}")
                        category = sub.get("category")
                        if category:
                            sections.append(f"Catégorie : {category}")
        except Exception as exc:
            logger.debug("Failed to parse JSON-LD: %s", exc)
    return sections


def _find_text_keys(obj: Any, collected: list[str], max_depth: int = 5) -> None:
    """Recursively search for marketing and product text in nested JSON hydration structures."""
    if max_depth <= 0:
        return
    if isinstance(obj, dict):
        target_keys = {
            "title", "name", "description", "details", "features", "benefits",
            "claims", "bulletpoints", "highlights", "subheading", "tagline",
            "materials", "ingredients", "sustainability", "ecocert"
        }
        for k, v in obj.items():
            k_lower = str(k).lower()
            if any(t in k_lower for t in target_keys):
                if isinstance(v, str) and len(v.strip()) > 3:
                    collected.append(v.strip())
                elif isinstance(v, list):
                    for elem in v:
                        if isinstance(elem, str) and len(elem.strip()) > 3:
                            collected.append(elem.strip())
            _find_text_keys(v, collected, max_depth - 1)
    elif isinstance(obj, list):
        for elem in obj:
            _find_text_keys(elem, collected, max_depth - 1)


def extract_hydration_data(soup: BeautifulSoup) -> list[str]:
    """Extract product state from Next.js, Nuxt, and Shopify client hydration tags."""
    sections: list[str] = []

    # 1. Next.js __NEXT_DATA__
    for tag in soup.find_all("script", id="__NEXT_DATA__"):
        if not tag.string:
            continue
        try:
            data = json.loads(tag.string)
            texts: list[str] = []
            props = data.get("props", {}).get("pageProps", {})
            _find_text_keys(props, texts)
            if texts:
                sections.append("Spécifications dynamiques (Next.js / Hydrogen) :")
                sections.extend(list(dict.fromkeys(texts))[:30])
        except Exception as exc:
            logger.debug("Failed to parse __NEXT_DATA__: %s", exc)

    # 2. Shopify meta / product JSON
    for tag in soup.find_all("script"):
        content = tag.string or ""
        if "ShopifyAnalytics" in content or "var meta =" in content or "window.__initialData" in content:
            # Match JSON object assignments
            match = re.search(r'(?:var\s+meta|window\.__initialData)\s*=\s*({.*?});', content, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(1))
                    texts: list[str] = []
                    _find_text_keys(data, texts)
                    if texts:
                        sections.append("Données produit (Shopify Storefront) :")
                        sections.extend(list(dict.fromkeys(texts))[:20])
                except Exception:
                    pass

    return sections


def execute_sandboxed_client_scripts(soup: BeautifulSoup, timeout_sec: float = 2.0) -> list[str]:
    """Execute client-side DOM mutation scripts in a sandboxed Node.js VM context."""
    sections: list[str] = []
    dom_scripts: list[str] = []

    for tag in soup.find_all("script"):
        script_code = tag.string or ""
        if not script_code.strip():
            continue
        # Check if the script modifies innerHTML or assigns product data
        if any(pat in script_code for pat in ("innerHTML", "textContent", "innerText", "document.write")):
            dom_scripts.append(script_code)

    if not dom_scripts:
        return sections

    combined_code = "\n".join(dom_scripts)
    node_runner = f"""
const vm = require('vm');
const sandbox = {{
  elements: {{}},
  document: {{
    title: '',
    getElementById(id) {{
      if (!sandbox.elements[id]) sandbox.elements[id] = {{ innerHTML: '', textContent: '' }};
      return sandbox.elements[id];
    }},
    querySelector(sel) {{
      if (!sandbox.elements[sel]) sandbox.elements[sel] = {{ innerHTML: '', textContent: '' }};
      return sandbox.elements[sel];
    }},
    write(str) {{
      if (!sandbox.elements['body']) sandbox.elements['body'] = {{ innerHTML: '', textContent: '' }};
      sandbox.elements['body'].innerHTML += str;
    }}
  }}
}};
sandbox.window = sandbox;
vm.createContext(sandbox);

try {{
  vm.runInContext({json.dumps(combined_code)}, sandbox, {{ timeout: 1500 }});
  const output = [];
  for (const [id, el] of Object.entries(sandbox.elements)) {{
    if (el.innerHTML) output.push(el.innerHTML);
    if (el.textContent && el.textContent !== el.innerHTML) output.push(el.textContent);
  }}
  console.log(JSON.stringify(output));
}} catch (e) {{
  console.log(JSON.stringify([]));
}}
"""
    try:
        proc = subprocess.run(
            ["node", "-e", node_runner],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            rendered_fragments = json.loads(proc.stdout.strip())
            for frag in rendered_fragments:
                # Clean html tags from rendered fragments
                clean_frag = BeautifulSoup(frag, "html.parser").get_text(separator="\n").strip()
                if clean_frag and len(clean_frag) > 10:
                    sections.append(clean_frag)
    except Exception as exc:
        logger.debug("Sandboxed Node script execution skipped or timed out: %s", exc)

    return sections
