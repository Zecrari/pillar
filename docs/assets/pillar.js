/* ═══════════════════════════════════════════════════════════════
   Pillar Framework — Shared JavaScript
   ═══════════════════════════════════════════════════════════════ */

(function () {
  "use strict";

  /* ── Dark-mode ─────────────────────────────────────────────── */
  const html  = document.documentElement;
  const saved = localStorage.getItem("pillar-theme");

  if (saved) {
    html.setAttribute("data-theme", saved);
  } else if (window.matchMedia("(prefers-color-scheme: dark)").matches) {
    html.setAttribute("data-theme", "dark");
  }

  function applyThemeIcon() {
    const btn = document.getElementById("themeToggle");
    if (!btn) return;
    btn.textContent = html.getAttribute("data-theme") === "dark" ? "☀️" : "🌙";
    btn.title = html.getAttribute("data-theme") === "dark"
      ? "Switch to light mode" : "Switch to dark mode";
  }

  document.addEventListener("DOMContentLoaded", function () {
    applyThemeIcon();

    const themeBtn = document.getElementById("themeToggle");
    if (themeBtn) {
      themeBtn.addEventListener("click", function () {
        const next = html.getAttribute("data-theme") === "dark" ? "light" : "dark";
        html.setAttribute("data-theme", next);
        localStorage.setItem("pillar-theme", next);
        applyThemeIcon();
      });
    }

    /* ── Mobile sidebar ─────────────────────────────────────── */
    const sidebar   = document.getElementById("pSidebar");
    const hamburger = document.getElementById("navHamburger");

    if (hamburger && sidebar) {
      hamburger.addEventListener("click", function () {
        sidebar.classList.toggle("open");
      });

      document.addEventListener("click", function (e) {
        if (!sidebar.contains(e.target) && !hamburger.contains(e.target)) {
          sidebar.classList.remove("open");
        }
      });

      // Close sidebar on link click (mobile)
      sidebar.querySelectorAll("a").forEach(function (a) {
        a.addEventListener("click", function () {
          if (window.innerWidth < 820) sidebar.classList.remove("open");
        });
      });
    }

    /* ── Active sidebar link on scroll ──────────────────────── */
    const sections = document.querySelectorAll("section[id], div[id].scroll-target");
    const sbLinks  = document.querySelectorAll(".p-sidebar a[href^='#']");
    const NAV_H    = 48;

    if (sections.length && sbLinks.length) {
      const io = new IntersectionObserver(
        function (entries) {
          entries.forEach(function (entry) {
            if (entry.isIntersecting) {
              sbLinks.forEach(function (l) { l.classList.remove("active"); });
              const sel = `.p-sidebar a[href="#${entry.target.id}"]`;
              const active = document.querySelector(sel);
              if (active) {
                active.classList.add("active");
                active.scrollIntoView({ block: "nearest", behavior: "smooth" });
              }
            }
          });
        },
        { rootMargin: `-${NAV_H + 8}px 0px -65% 0px`, threshold: 0 }
      );

      sections.forEach(function (s) { io.observe(s); });
    }

    /* ── Active nav link based on page ─────────────────────── */
    const navLinks = document.querySelectorAll(".nav-links a");
    navLinks.forEach(function (a) {
      const href = a.getAttribute("href");
      if (!href) return;
      const page = href.split("/").pop().split("?")[0];
      const cur  = window.location.pathname.split("/").pop() || "index.html";
      if (page === cur || (page === "index.html" && cur === "")) {
        a.classList.add("nav-active");
      }
    });

    /* ── Copy buttons ───────────────────────────────────────── */
    document.querySelectorAll(".code-block").forEach(function (block) {
      const toolbar = block.querySelector(".code-toolbar");
      if (!toolbar) return;

      const btn = document.createElement("button");
      btn.className   = "code-copy";
      btn.textContent = "copy";
      toolbar.appendChild(btn);

      btn.addEventListener("click", function () {
        const code = block.querySelector("code");
        if (!code) return;
        navigator.clipboard.writeText(code.innerText.trim()).then(function () {
          btn.textContent = "copied!";
          btn.classList.add("ok");
          setTimeout(function () {
            btn.textContent = "copy";
            btn.classList.remove("ok");
          }, 2000);
        }).catch(function () {
          // Fallback for older browsers
          const ta = document.createElement("textarea");
          ta.value = code.innerText.trim();
          document.body.appendChild(ta);
          ta.select();
          document.execCommand("copy");
          document.body.removeChild(ta);
          btn.textContent = "copied!";
          btn.classList.add("ok");
          setTimeout(function () {
            btn.textContent = "copy";
            btn.classList.remove("ok");
          }, 2000);
        });
      });
    });

    /* ── Highlight.js ───────────────────────────────────────── */
    if (window.hljs) {
      hljs.highlightAll();
    }

    /* ── Smooth scroll for anchor links ─────────────────────── */
    document.querySelectorAll('a[href^="#"]').forEach(function (a) {
      a.addEventListener("click", function (e) {
        const id = a.getAttribute("href").slice(1);
        const el = document.getElementById(id);
        if (el) {
          e.preventDefault();
          el.scrollIntoView({ behavior: "smooth" });
        }
      });
    });
  });
})();
