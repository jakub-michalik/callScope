// Populate the sidebar version switcher from /<repo>/versions.json.
// "latest" lives at the Pages root; tagged releases live in /<tag>/ subdirs.
(function () {
  var BASE = "/callScope/";
  var sel = document.getElementById("vswitch");
  if (!sel) return;
  fetch(BASE + "versions.json")
    .then(function (r) { return r.ok ? r.json() : []; })
    .then(function (versions) {
      if (!versions.length) return;
      var path = window.location.pathname;
      sel.innerHTML = "";
      versions.forEach(function (v) {
        var o = document.createElement("option");
        o.value = v.url;
        o.textContent = v.name;
        var current = v.name === "latest"
          ? (path === v.url || path === v.url + "index.html"
             || path.indexOf(BASE + "v") !== 0)
          : path.indexOf(v.url) === 0;
        if (current) o.selected = true;
        sel.appendChild(o);
      });
      sel.addEventListener("change", function () {
        window.location.href = sel.value;
      });
    })
    .catch(function () { /* versions.json not deployed yet — leave default */ });
})();
