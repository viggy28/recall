"""Self-contained HTML graph explorer for ``recall graph --format html``.

Renders the graph as a single dependency-free HTML file: an embedded
force-directed layout (vanilla JS + SVG), with no CDN or network access, so the
file works offline when opened directly in a browser. The graph JSON is embedded
inline; nothing leaves the machine.
"""
from __future__ import annotations

import json


def render_html(graph) -> str:
    """Return a standalone HTML document visualizing ``graph``."""
    # The explorer renders node provenance but not per-edge references, so drop
    # edge references (the bulk of the payload for dense graphs) and bound each
    # node's reference list to what the detail panel can show.
    slim = {
        "nodes": [
            {**node, "references": node.get("references", [])[:50]}
            for node in graph.get("nodes", [])
        ],
        "edges": [
            {"source": edge["source"], "target": edge["target"], "weight": edge["weight"]}
            for edge in graph.get("edges", [])
        ],
        "meta": graph.get("meta", {}),
    }
    data = json.dumps(slim, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return _TEMPLATE.replace("__DATA__", data)


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Recall graph</title>
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; }
body { background: #0d1117; color: #e6edf3; font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
#app { display: flex; height: 100vh; }
#graph { flex: 1; min-width: 0; position: relative; }
#plot { width: 100%; height: 100%; display: block; background: radial-gradient(circle at 50% 50%, #11161d 0%, #0d1117 70%); }
#panel { width: 340px; border-left: 1px solid #21262d; background: #161b22; display: flex; flex-direction: column; }
#panel-header { padding: 12px 16px; border-bottom: 1px solid #21262d; }
#panel-header h1 { font-size: 16px; margin: 0 0 2px; }
#counts { color: #8b949e; font-size: 12px; }
.controls { padding: 12px 16px; border-bottom: 1px solid #21262d; }
.controls label.row { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; font-size: 13px; color: #c9d1d9; }
#search { flex: 1; background: #0d1117; color: #e6edf3; border: 1px solid #30363d; border-radius: 6px; padding: 6px 8px; }
#min-weight { flex: 1; }
#legend { display: flex; flex-wrap: wrap; gap: 6px 12px; }
#legend label { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; color: #c9d1d9; cursor: pointer; }
#legend .dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
#panel-body { flex: 1; overflow-y: auto; padding: 4px 16px 16px; }
#panel-body h2 { font-size: 15px; margin: 12px 0 2px; overflow-wrap: anywhere; }
#panel-body .meta { color: #8b949e; font-size: 12px; margin-bottom: 6px; }
#panel-body h3 { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: #8b949e; margin: 16px 0 6px; }
#panel-body ul { list-style: none; margin: 0; padding: 0; }
#panel-body li { padding: 3px 0; font-size: 13px; border-bottom: 1px solid #21262d; overflow-wrap: anywhere; }
#panel-body a { color: #58a6ff; text-decoration: none; }
#panel-body a:hover { text-decoration: underline; }
#panel-body .w { color: #8b949e; font-size: 12px; }
#panel-body .refs li { color: #8b949e; font-size: 12px; }
#hint { position: absolute; left: 12px; bottom: 10px; color: #8b949e; font-size: 12px; pointer-events: none; }
</style>
</head>
<body>
<div id="app">
  <div id="graph">
    <svg id="plot">
      <g id="viewport"><g id="edges"></g><g id="nodes"></g><g id="labels"></g></g>
    </svg>
    <div id="hint">drag to pan · scroll to zoom · drag a node to move · hover to focus · click for details</div>
  </div>
  <aside id="panel">
    <div id="panel-header"><h1>Recall graph</h1><div id="counts"></div></div>
    <div class="controls">
      <label class="row">Search <input id="search" type="text" placeholder="filter entities…"></label>
      <label class="row">Min edge weight <span id="min-weight-val">1</span> <input id="min-weight" type="range" min="1" max="10" value="1"></label>
      <div id="legend"></div>
    </div>
    <div id="panel-body"><p style="color:#8b949e">Click a node to see its connections and source references.</p></div>
  </aside>
</div>
<script>
(function () {
  var data = __DATA__;
  var nodes = data.nodes.map(function (n) { return Object.assign({}, n); });
  var edges = data.edges.map(function (e) { return Object.assign({}, e); });
  var byId = new Map(nodes.map(function (n) { return [n.id, n]; }));
  var adj = new Map();
  nodes.forEach(function (n) { adj.set(n.id, []); });
  edges.forEach(function (e) {
    adj.get(e.source).push({ id: e.target, weight: e.weight });
    adj.get(e.target).push({ id: e.source, weight: e.weight });
  });

  var COLORS = {
    organization: "#58a6ff", technology: "#3fb950", file: "#d29922",
    person: "#bc8cff", topic: "#f778ba", reference: "#8b949e", entity: "#ff7b72"
  };
  function colorOf(n) { return COLORS[n.type] || "#8b949e"; }
  function radiusOf(n) { return Math.min(22, Math.max(3, 3 + Math.sqrt(n.mentions) * 1.5)); }
  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  var W = 1000, H = 700;
  var NS = "http://www.w3.org/2000/svg";
  var svg = document.getElementById("plot");
  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  var viewport = document.getElementById("viewport");
  var edgeLayer = document.getElementById("edges");
  var nodeLayer = document.getElementById("nodes");
  var labelLayer = document.getElementById("labels");

  // ---- Fruchterman-Reingold layout ----
  function layout() {
    var k = Math.sqrt((W * H) / Math.max(1, nodes.length));
    nodes.forEach(function (n) {
      n.x = W / 2 + (Math.random() - 0.5) * k * 5;
      n.y = H / 2 + (Math.random() - 0.5) * k * 5;
    });
    var temp = W / 8;
    while (temp > 0.5) {
      nodes.forEach(function (n) { n.dx = 0; n.dy = 0; });
      for (var i = 0; i < nodes.length; i++) {
        for (var j = i + 1; j < nodes.length; j++) {
          var a = nodes[i], b = nodes[j];
          var dx = b.x - a.x, dy = b.y - a.y;
          var d = Math.sqrt(dx * dx + dy * dy) || 0.01;
          var f = (k * k) / d;
          a.dx -= (dx / d) * f; a.dy -= (dy / d) * f;
          b.dx += (dx / d) * f; b.dy += (dy / d) * f;
        }
      }
      edges.forEach(function (e) {
        var a = byId.get(e.source), b = byId.get(e.target);
        if (!a || !b) return;
        var dx = b.x - a.x, dy = b.y - a.y;
        var d = Math.sqrt(dx * dx + dy * dy) || 0.01;
        var f = (d * d) / k;
        a.dx += (dx / d) * f; a.dy += (dy / d) * f;
        b.dx -= (dx / d) * f; b.dy -= (dy / d) * f;
      });
      nodes.forEach(function (n) {
        var d = Math.sqrt(n.dx * n.dx + n.dy * n.dy) || 0.01;
        var m = Math.min(d, temp);
        n.x += (n.dx / d) * m;
        n.y += (n.dy / d) * m;
        n.x = Math.min(W - 24, Math.max(24, n.x));
        n.y = Math.min(H - 24, Math.max(24, n.y));
      });
      temp *= 0.95;
    }
  }

  // ---- filters ----
  var state = { minWeight: 1, search: "", types: new Set(Object.keys(COLORS)) };
  function visibleNode(n) {
    if (!state.types.has(n.type)) return false;
    if (state.search && (n.label || n.id).toLowerCase().indexOf(state.search) === -1) return false;
    return true;
  }
  function visibleEdge(e) {
    if (e.weight < state.minWeight) return false;
    var a = byId.get(e.source), b = byId.get(e.target);
    return !!(a && b && visibleNode(a) && visibleNode(b));
  }

  var focus = null, selected = null;
  function neighborhood(id) {
    var set = new Set([id]);
    (adj.get(id) || []).forEach(function (nb) { set.add(nb.id); });
    return set;
  }

  function render() {
    edgeLayer.textContent = ""; nodeLayer.textContent = ""; labelLayer.textContent = "";
    var maxW = Math.max(1, Math.max.apply(null, edges.map(function (e) { return e.weight; })));
    var focusSet = focus ? neighborhood(focus) : null;

    edges.forEach(function (e) {
      if (!visibleEdge(e)) return;
      var a = byId.get(e.source), b = byId.get(e.target);
      var line = document.createElementNS(NS, "line");
      line.setAttribute("x1", a.x); line.setAttribute("y1", a.y);
      line.setAttribute("x2", b.x); line.setAttribute("y2", b.y);
      line.setAttribute("stroke", "#3d444d");
      line.setAttribute("stroke-width", (0.4 + (e.weight / maxW) * 2.5).toFixed(2));
      var op = 0.18 + (e.weight / maxW) * 0.55;
      if (focusSet) {
        var touches = focusSet.has(e.source) || focusSet.has(e.target);
        op = touches ? 0.9 : 0.03;
      }
      line.setAttribute("opacity", op.toFixed(3));
      edgeLayer.appendChild(line);
    });

    nodes.forEach(function (n) {
      if (!visibleNode(n)) return;
      var g = document.createElementNS(NS, "g");
      var c = document.createElementNS(NS, "circle");
      var r = radiusOf(n);
      c.setAttribute("r", r);
      c.setAttribute("fill", colorOf(n));
      var op = 0.85;
      if (focusSet) op = focusSet.has(n.id) ? 1 : 0.12;
      c.setAttribute("opacity", op.toFixed(2));
      c.setAttribute("stroke", selected === n.id ? "#f0f6fc" : "#0d1117");
      c.setAttribute("stroke-width", selected === n.id ? "2.5" : "1");
      g.setAttribute("transform", "translate(" + n.x + "," + n.y + ")");
      g.style.cursor = "grab";
      g.addEventListener("mouseenter", function () { focus = n.id; render(); });
      g.addEventListener("mouseleave", function () { focus = null; render(); });
      g.addEventListener("click", function (ev) { ev.stopPropagation(); select(n.id); });
      g.addEventListener("mousedown", function (ev) { ev.stopPropagation(); startNodeDrag(ev, n); });
      g.appendChild(c);
      nodeLayer.appendChild(g);

      var showLabel = selected === n.id || n.mentions >= 8 || focus === n.id;
      if (showLabel) {
        var t = document.createElementNS(NS, "text");
        t.setAttribute("x", n.x);
        t.setAttribute("y", n.y - r - 4);
        t.setAttribute("text-anchor", "middle");
        t.setAttribute("font-size", Math.min(13, 8 + n.mentions / 12));
        t.setAttribute("fill", selected === n.id ? "#f0f6fc" : "#c9d1d9");
        t.textContent = n.label;
        labelLayer.appendChild(t);
      }
    });
  }

  function select(id) {
    selected = id;
    var n = byId.get(id);
    var panel = document.getElementById("panel-body");
    var neighbors = (adj.get(id) || []).slice().sort(function (a, b) { return b.weight - a.weight; }).slice(0, 12);
    var html = "<h2>" + escapeHtml(n.label) + "</h2>";
    html += "<div class='meta'>" + n.type + " · " + n.mentions + " mentions · " + (adj.get(id) || []).length + " links</div>";
    html += "<h3>Connected</h3><ul>";
    neighbors.forEach(function (nb) {
      var other = byId.get(nb.id);
      html += "<li><a href='#' data-id='" + escapeHtml(nb.id) + "'>" + escapeHtml(other ? other.label : nb.id) + "</a> <span class='w'>×" + nb.weight + "</span></li>";
    });
    html += "</ul><h3>Mentioned in</h3><ul class='refs'>";
    (n.references || []).slice(0, 20).forEach(function (r) {
      html += "<li>" + escapeHtml(
        (r.session_id || "").slice(0, 8) + " · " + (r.source || "?") + " · " + (r.path || "?") + ":" + (r.line_no != null ? r.line_no : "?") + " · " + (r.timestamp || "").slice(0, 10)
      ) + "</li>";
    });
    html += "</ul>";
    panel.innerHTML = html;
    panel.querySelectorAll("a[data-id]").forEach(function (a) {
      a.addEventListener("click", function (ev) {
        ev.preventDefault();
        focus = a.dataset.id;
        select(a.dataset.id);
      });
    });
    render();
  }

  // ---- pan / zoom / node drag ----
  var t = { x: 0, y: 0, k: 1 };
  function applyTransform() {
    viewport.setAttribute("transform", "translate(" + t.x + "," + t.y + ") scale(" + t.k + ")");
  }
  function toLayout(clientX, clientY) {
    var rect = svg.getBoundingClientRect();
    var sx = (clientX - rect.left) * (W / rect.width);
    var sy = (clientY - rect.top) * (H / rect.height);
    return { x: (sx - t.x) / t.k, y: (sy - t.y) / t.k };
  }

  var dragNode = null, dragOff = null, panning = false, panStart = null;
  function startNodeDrag(ev, n) {
    ev.preventDefault();
    dragNode = n;
    var p = toLayout(ev.clientX, ev.clientY);
    dragOff = { x: p.x - n.x, y: p.y - n.y };
  }
  window.addEventListener("mousemove", function (ev) {
    if (dragNode) {
      var p = toLayout(ev.clientX, ev.clientY);
      dragNode.x = p.x - dragOff.x;
      dragNode.y = p.y - dragOff.y;
      render();
    } else if (panning) {
      t.x = panStart.tx + (ev.clientX - panStart.x);
      t.y = panStart.ty + (ev.clientY - panStart.y);
      applyTransform();
    }
  });
  window.addEventListener("mouseup", function () { dragNode = null; panning = false; });

  svg.addEventListener("mousedown", function (ev) {
    if (ev.target === svg || ev.target === viewport) {
      panning = true;
      panStart = { x: ev.clientX, y: ev.clientY, tx: t.x, ty: t.y };
    }
  });
  svg.addEventListener("wheel", function (ev) {
    ev.preventDefault();
    var factor = ev.deltaY < 0 ? 1.1 : 1 / 1.1;
    var rect = svg.getBoundingClientRect();
    var px = (ev.clientX - rect.left) * (W / rect.width);
    var py = (ev.clientY - rect.top) * (H / rect.height);
    var k2 = Math.min(4, Math.max(0.1, t.k * factor));
    t.x = px - ((px - t.x) / t.k) * k2;
    t.y = py - ((py - t.y) / t.k) * k2;
    t.k = k2;
    applyTransform();
  }, { passive: false });

  // ---- controls ----
  var weightSlider = document.getElementById("min-weight");
  weightSlider.max = Math.max(1, Math.max.apply(null, edges.map(function (e) { return e.weight; })));
  weightSlider.addEventListener("input", function () {
    state.minWeight = Number(weightSlider.value);
    document.getElementById("min-weight-val").textContent = weightSlider.value;
    render();
  });
  document.getElementById("search").addEventListener("input", function (ev) {
    state.search = ev.target.value.trim().toLowerCase();
    render();
  });
  var legend = document.getElementById("legend");
  Object.keys(COLORS).forEach(function (type) {
    var label = document.createElement("label");
    var cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = true;
    cb.addEventListener("change", function () {
      if (cb.checked) state.types.add(type); else state.types.delete(type);
      render();
    });
    label.appendChild(cb);
    var dot = document.createElement("span");
    dot.className = "dot"; dot.style.background = COLORS[type];
    label.appendChild(dot);
    label.appendChild(document.createTextNode(type));
    legend.appendChild(label);
  });

  document.getElementById("counts").textContent = nodes.length + " nodes · " + edges.length + " edges";
  layout();
  render();
  applyTransform();
})();
</script>
</body>
</html>
"""
