// Renders data/summary.json. Every number on the page comes from that file;
// nothing is re-aggregated here. Wording and layout only.
(function () {
  "use strict";

  var DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  var DAY_NAMES = {
    Mon: "Monday", Tue: "Tuesday", Wed: "Wednesday", Thu: "Thursday",
    Fri: "Friday", Sat: "Saturday", Sun: "Sunday"
  };
  var BINS = [20, 35, 50, 65, 80]; // upper edges; six steps in all
  var BIN_LABELS = ["under 20%", "20-34%", "35-49%", "50-64%", "65-79%", "80%+"];
  var DEFAULT_ROOM = "Strength Training";

  var $ = function (id) { return document.getElementById(id); };
  var summary = null;
  var minN = 3;

  // Locally, serve the repo root and open /docs/, so ../data/ is the live
  // file. On GitHub Pages (served from /docs) that path does not exist, so
  // fall back to the same file on the default branch, read straight from the
  // repo the page is hosted from.
  function sources() {
    var list = ["../data/summary.json"];
    var host = location.hostname.match(/^([^.]+)\.github\.io$/);
    var repo = location.pathname.split("/").filter(Boolean)[0];
    if (host && repo) {
      list.push("https://raw.githubusercontent.com/" + host[1] + "/" + repo + "/main/data/summary.json");
    }
    return list;
  }

  function load(urls) {
    if (!urls.length) return Promise.reject(new Error("no source"));
    return fetch(urls[0], { cache: "no-cache" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .catch(function () { return load(urls.slice(1)); });
  }

  function bin(pct) {
    for (var i = 0; i < BINS.length; i++) if (pct < BINS[i]) return i;
    return BINS.length;
  }

  function hourLabel(h) {
    h = ((h % 24) + 24) % 24;
    return (h % 12 || 12) + (h < 12 ? " am" : " pm");
  }
  function shortHour(h) {
    h = ((h % 24) + 24) % 24;
    return (h % 12 || 12) + (h < 12 ? "a" : "p");
  }
  // "6-8 am", "4-6 pm", "11 am-1 pm", "10 am-noon".
  function rangeLabel(start, end) {
    if (end === 12) return hourLabel(start).replace(" am", "") + " am-noon";
    if (end === 24) return hourLabel(start) + "-midnight";
    var sameHalf = (start < 12 && end < 12) || (start >= 12 && end > 12);
    if (sameHalf) return (start % 12 || 12) + "-" + (end % 12 || 12) + (start < 12 ? " am" : " pm");
    return hourLabel(start) + "-" + hourLabel(end);
  }

  function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }

  function fmtDate(iso, withYear) {
    var p = iso.split("-").map(Number);
    var d = new Date(Date.UTC(p[0], p[1] - 1, p[2]));
    var opts = { month: "long", day: "numeric", timeZone: "UTC" };
    if (withYear) opts.year = "numeric";
    return d.toLocaleDateString("en-US", opts);
  }

  function el(tag, attrs, text) {
    var node = document.createElement(tag);
    if (attrs) for (var k in attrs) node.setAttribute(k, attrs[k]);
    if (text != null) node.textContent = text;
    return node;
  }

  function fillRooms() {
    var sel = $("room");
    var names = Object.keys(summary.locations).sort();
    var featured = (summary.featured || []).filter(function (n) { return names.indexOf(n) >= 0; });
    var rest = names.filter(function (n) { return featured.indexOf(n) < 0; });
    function group(label, list) {
      if (!list.length) return;
      var g = el("optgroup", { label: label });
      list.forEach(function (n) { g.appendChild(el("option", { value: n }, n)); });
      sel.appendChild(g);
    }
    group("Lifting and cardio", featured);
    group("Other rooms", rest);

    var fromHash = decodeURIComponent((location.hash.match(/room=([^&]+)/) || [])[1] || "");
    sel.value = names.indexOf(fromHash) >= 0 ? fromHash
      : names.indexOf(DEFAULT_ROOM) >= 0 ? DEFAULT_ROOM : names[0];
    sel.addEventListener("change", function () {
      history.replaceState(null, "", "#room=" + encodeURIComponent(sel.value));
      render(sel.value);
    });
  }

  function windowText(w, kind) {
    var when = DAY_NAMES[w.day] + " " + rangeLabel(w.start, w.end);
    if (kind === "best") {
      var under = Math.floor(w.max_pct / 5) * 5 + 5;
      return [when, " is usually under " + under + "% full."];
    }
    var over = Math.ceil(w.min_pct / 5) * 5 - 5;
    return [when, " is usually over " + Math.max(over, 0) + "% full."];
  }

  function renderWindows(loc) {
    var w = loc.windows;
    var box = $("windows");
    if (!w) { box.hidden = true; return; }
    box.hidden = false;
    [["best", $("best")], ["worst", $("worst")]].forEach(function (pair) {
      var ul = pair[1];
      ul.textContent = "";
      var list = w[pair[0]] || [];
      if (!list.length) {
        ul.appendChild(el("li", null, "Not enough readings yet. No hour in this room has " + minN + " or more."));
        return;
      }
      list.forEach(function (item) {
        var parts = windowText(item, pair[0]);
        var li = el("li");
        li.appendChild(el("strong", null, parts[0]));
        li.appendChild(document.createTextNode(parts[1]));
        li.appendChild(el("span", { class: "n" }, "Based on " + plural(item.n, "reading") + "."));
        ul.appendChild(li);
      });
    });
  }

  function describe(name, day, hour, cell, capacity) {
    var when = DAY_NAMES[day] + " " + hourLabel(hour);
    if (!cell) return when + ": no readings yet. The Rec may be closed then, or it did not post a count.";
    var count = capacity ? " (about " + cell.count + " of " + capacity + " people)" : "";
    var trust = cell.n < minN
      ? " Too few to trust yet; this needs at least " + minN + "."
      : "";
    return when + ": usually " + cell.pct + "% full" + count + ". " +
      plural(cell.n, "reading") + "." + trust;
  }

  function renderGrid(name, loc) {
    var grid = $("grid");
    grid.textContent = "";
    var hours = [];
    DAYS.forEach(function (d) {
      Object.keys(loc.grid[d] || {}).forEach(function (h) {
        h = Number(h);
        if (hours.indexOf(h) < 0) hours.push(h);
      });
    });
    if (!hours.length) return;
    var lo = Math.min.apply(null, hours), hi = Math.max.apply(null, hours);

    grid.appendChild(el("div", { class: "head", role: "presentation" }, ""));
    DAYS.forEach(function (d) {
      grid.appendChild(el("div", { class: "head", role: "columnheader" }, d));
    });

    var selected = null;
    function show(btn, text) {
      if (selected) selected.classList.remove("selected");
      selected = btn;
      btn.classList.add("selected");
      $("detail").textContent = text;
    }

    for (var h = lo; h <= hi; h++) {
      grid.appendChild(el("div", { class: "hour", role: "rowheader" }, shortHour(h)));
      DAYS.forEach(function (d) {
        var cell = (loc.grid[d] || {})[String(h)];
        var text = describe(name, d, h, cell, loc.capacity);
        var cls = "cell " + (cell ? "b" + bin(cell.pct) + (cell.n < minN ? " thin" : "") : "none");
        var btn = el("button", { type: "button", class: cls, role: "gridcell", "aria-label": text, title: text },
          cell ? String(cell.pct) : "");
        btn.addEventListener("click", function () { show(btn, text); });
        btn.addEventListener("focus", function () { show(btn, text); });
        btn.addEventListener("mouseenter", function () { show(btn, text); });
        grid.appendChild(btn);
      });
    }
    $("detail").textContent = "Tap a cell for details.";
  }

  function renderScale() {
    var box = $("scale");
    box.textContent = "";
    BIN_LABELS.forEach(function (label, i) {
      var s = el("span", null, label);
      s.style.background = "var(--h" + i + ")";
      s.style.color = "var(--t" + i + ")";
      box.appendChild(s);
    });
    $("thin-label").textContent = "Fewer than " + minN +
      " readings: washed out with a dashed edge. Treat it as a guess.";
  }

  function renderCoverage() {
    var c = summary.coverage || {};
    var body = $("coverage-body");
    body.textContent = "";
    var first = c.window_starts || summary.first_day;
    var last = summary.last_day;
    if (first && last) {
      body.appendChild(el("p", null,
        "Readings from " + fmtDate(first) + " to " + fmtDate(last, true) + ": " +
        plural(c.days_observed || summary.days_observed, "day") + " with data out of " +
        (c.days_spanned || "?") + ", " + summary.samples.toLocaleString("en-US") + " room counts in all."));
    }

    var games = c.explained_days || {};
    var gameDays = Object.keys(games).sort();
    if (gameDays.length) {
      body.appendChild(el("p", null,
        "The Rec closes for home football games, so these days have no data at all:"));
      var ul = el("ul");
      gameDays.forEach(function (d) {
        ul.appendChild(el("li", null, fmtDate(d) + " (" + games[d].replace(/^home game vs /, "home game against ") + ")"));
      });
      body.appendChild(ul);
    }
    var unexplained = c.unexplained_days || [];
    if (unexplained.length) {
      body.appendChild(el("p", null, "Also missing, with no known reason: " +
        unexplained.map(function (d) { return fmtDate(d); }).join(", ") + "."));
    }
    var next = c.next_home_games || [];
    if (next.length) {
      body.appendChild(el("p", null, "Next home game: " + fmtDate(next[0], true) + ". Expect the Rec to be closed that day."));
    }

    body.appendChild(el("p", null,
      "The Rec posts counts on its own schedule: sometimes every few minutes, sometimes hours apart, " +
      "and evenings are patchy. So some hours have many readings and others have one. " +
      "A cell needs at least " + minN + " readings before it is shown at full strength, " +
      "and the best and worst times above only use cells that clear that bar."));
    body.appendChild(el("p", null,
      "Percent full uses the Rec's own capacity for each room. Closed rooms are left out rather than counted as empty. " +
      "Last rebuilt " + new Date(summary.generated_at).toLocaleString("en-US",
        { month: "long", day: "numeric", hour: "numeric", minute: "2-digit", timeZone: "America/Chicago" }) +
      " Columbia time."));
  }

  function render(name) {
    var loc = summary.locations[name];
    $("heat-title").textContent = name + ", week by hour";
    renderWindows(loc);
    renderGrid(name, loc);
  }

  load(sources()).then(function (data) {
    summary = data;
    if (typeof data.min_samples === "number") minN = data.min_samples;
    $("status").hidden = true;
    $("heat").hidden = false;
    $("coverage").hidden = false;
    fillRooms();
    renderScale();
    renderCoverage();
    render($("room").value);
  }).catch(function () {
    $("status").textContent = "Could not load the data. If you opened this file directly, serve the repo instead: " +
      "run python3 -m http.server in the repo folder and open localhost:8000/docs/.";
  });
})();
