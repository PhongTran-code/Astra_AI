/* ═══════════════════════════════════════════════════════════════════
   ASTRA LANDING — theme toggle · terminal typewriter · scroll reveal ·
   nav-pill scroll state · case-card spotlight
   ═══════════════════════════════════════════════════════════════════ */

// ─── Theme toggle ──────────────────────────────────────────────
(function themeInit() {
  const html = document.documentElement;
  const saved = localStorage.getItem('astra-theme');
  if (saved === 'light' || saved === 'dark') html.setAttribute('data-theme', saved);

  const btn = document.getElementById('themeToggle');
  if (!btn) return;
  btn.addEventListener('click', () => {
    const next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    html.setAttribute('data-theme', next);
    localStorage.setItem('astra-theme', next);
  });
})();


// ─── Enclave Architecture Interactive Engine ───────────────────
(function enclaveEngine() {
  const root = document.getElementById('pdiagramRoot');
  if (!root) return;

  const scenarioPills = root.querySelectorAll('.pd-pill');
  const nodes = root.querySelectorAll('.enode');
  const gateDot = document.getElementById('gateDot');
  const gateStatus = document.getElementById('gateStatus');


  // Node Selection & Active Route
  nodes.forEach((n) => {
    n.addEventListener('click', () => {
      nodes.forEach((item) => {
        const isActive = item === n;
        item.classList.toggle('is-active', isActive);
        item.setAttribute('aria-pressed', String(isActive));
      });
      const key = n.getAttribute('data-node');
      if (key) root.dataset.activeNode = key;
    });
  });

  // Scenario state and governance signal
  const SCENARIOS = {
    loop: {
      mode: 'mode-loop',
      gateStatus: 'IDLE (LOCAL)',
      gateColor: '#10b981'
    },
    gate: {
      mode: 'mode-gate',
      gateStatus: 'ARMED (APPROVAL)',
      gateColor: 'var(--warn)'
    },
    block: {
      mode: 'mode-block',
      gateStatus: 'HARD FLOOR TRIPPED',
      gateColor: '#ef4444'
    }
  };


  function setScenario(scKey) {
    const sc = SCENARIOS[scKey];
    if (!sc) return;

    scenarioPills.forEach((p) => {
      const isActive = p.getAttribute('data-scenario') === scKey;
      p.classList.toggle('is-active', isActive);
      p.setAttribute('aria-pressed', String(isActive));
    });

    root.classList.remove('mode-loop', 'mode-gate', 'mode-block');
    root.classList.add(sc.mode);
    root.style.setProperty('--fabric-color', sc.gateColor);
    root.style.setProperty('--gate-color', sc.gateColor);

    if (gateStatus) {
      gateStatus.textContent = sc.gateStatus;
      gateStatus.style.color = sc.gateColor;
    }
    if (gateDot) {
      gateDot.style.backgroundColor = sc.gateColor;
    }
  }

  scenarioPills.forEach((p) => {
    p.addEventListener('click', () => {
      const key = p.getAttribute('data-scenario');
      if (key && SCENARIOS[key]) {
        setScenario(key);
      }
    });
  });


  setScenario('loop');
})();


// ─── Marquee: duplicate track so translate -50% loops seamlessly ─
(function connMarquee() {
  const track = document.querySelector('.conn-track');
  if (!track) return;

  // Clone all children once so the visible loop is 2x content and -50%
  // wraps exactly at the seam. Also set duration proportional to width
  // so speed is consistent regardless of how many logos exist.
  const original = [...track.children];
  original.forEach((el) => {
    const clone = el.cloneNode(true);
    clone.setAttribute('aria-hidden', 'true');
    track.appendChild(clone);
  });

  // Set animation duration ~14s per 1600px of track content — brisk pace.
  const totalWidth = track.scrollWidth;
  const secondsPer1600 = 14;
  const duration = Math.max(10, (totalWidth / 1600) * secondsPer1600);
  track.style.animationDuration = duration + 's';
})();


/* ─── Nav scrollspy ────────────────────────────────────────────
   Highlights the .nav-link (+ mobile .nd-link) whose section is
   most-visible, and glides the .nav-links sliding pill indicator
   (--nl-x / --nl-w custom props) under the active desktop link. */
(function navScrollspy() {
  const links = document.querySelectorAll('.nav-link[data-nav]');
  const drawerLinks = document.querySelectorAll('.nd-link[data-nav]');
  if (!links.length) return;

  const navList = document.querySelector('.nav-links');
  const map = new Map();
  const drawerMap = new Map();
  const sections = [];
  links.forEach((a) => {
    const id = a.getAttribute('data-nav');
    const s = document.getElementById(id);
    if (s) { map.set(id, a); sections.push(s); }
  });
  drawerLinks.forEach((a) => drawerMap.set(a.getAttribute('data-nav'), a));
  if (!sections.length) return;

  let currentId = null;

  function positionIndicator(a) {
    if (!navList || !a) return;
    const listRect = navList.getBoundingClientRect();
    // Underline only the label text (.nl-t), not the "01" index digits,
    // so it stays short and doesn't stretch across the whole button.
    const target = a.querySelector('.nl-t') || a;
    const targetRect = target.getBoundingClientRect();
    navList.style.setProperty('--nl-x', (targetRect.left - listRect.left) + 'px');
    navList.style.setProperty('--nl-w', targetRect.width + 'px');
    navList.style.setProperty('--nl-op', '1');
  }

  function setActive(id) {
    if (id === currentId) return;
    currentId = id;
    links.forEach((a) => a.classList.remove('is-active'));
    drawerLinks.forEach((a) => a.classList.remove('is-active'));
    const a = map.get(id);
    if (a) { a.classList.add('is-active'); positionIndicator(a); }
    const d = drawerMap.get(id);
    if (d) d.classList.add('is-active');
  }

  function update() {
    const targetY = window.innerHeight * 0.3;
    let best = null;
    let bestDist = Infinity;
    for (const s of sections) {
      const rect = s.getBoundingClientRect();
      if (rect.bottom < 0 || rect.top > window.innerHeight) continue;
      const dist = Math.abs(rect.top - targetY);
      if (dist < bestDist) { bestDist = dist; best = s.id; }
    }
    if (best) setActive(best);
  }
  window.addEventListener('scroll', update, { passive: true });
  window.addEventListener('resize', () => {
    // re-measure indicator position on resize (widths shift with layout)
    currentId = null;
    update();
  });
  update();
})();


/* ─── Mobile drawer toggle ─────────────────────────────────────── */
(function navMobile() {
  const btn = document.getElementById('navBurger');
  if (!btn) return;

  function close() {
    document.body.classList.remove('nav-open');
    btn.setAttribute('aria-expanded', 'false');
  }
  function toggle() {
    const open = document.body.classList.toggle('nav-open');
    btn.setAttribute('aria-expanded', String(open));
  }
  btn.addEventListener('click', toggle);
  document.querySelectorAll('.nd-link').forEach((a) => a.addEventListener('click', close));
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  // Close on click outside
  document.addEventListener('click', (e) => {
    if (!document.body.classList.contains('nav-open')) return;
    if (e.target.closest('.nav-pill') || e.target.closest('.nav-drawer')) return;
    close();
  });
})();


/* ─── FX-SYSTEM: page-enter + scroll-reveal riêng từng section ───
   - body.is-loaded: chạy intro khi vào web (nav + hero cascade)
   - [data-fx]: mỗi section 1 kiểu, không trùng (CSS quyết định kiểu)
   - Giữ tương thích .reveal cũ. Respects prefers-reduced-motion. */
(function pageEnter() {
  function markLoaded() {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => document.body.classList.add('is-loaded'));
    });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', markLoaded, { once: true });
  } else {
    markLoaded();
  }
  // Phòng trường hợp font/ảnh chậm: đảm bảo vẫn hiện sau 2.5s
  setTimeout(() => document.body.classList.add('is-loaded'), 2500);
})();

(function scrollProgress() {
  let bar = document.querySelector('.scroll-progress > i');
  if (!bar) {
    const wrap = document.createElement('div');
    wrap.className = 'scroll-progress';
    wrap.setAttribute('aria-hidden', 'true');
    bar = document.createElement('i');
    wrap.appendChild(bar);
    document.body.prepend(wrap);
  }
  let ticking = false;
  function apply() {
    const h = document.documentElement;
    const max = h.scrollHeight - h.clientHeight;
    const p = max > 0 ? (window.scrollY / max) : 0;
    bar.style.transform = 'scaleX(' + p.toFixed(4) + ')';
    ticking = false;
  }
  window.addEventListener('scroll', () => {
    if (!ticking) { ticking = true; requestAnimationFrame(apply); }
  }, { passive: true });
  apply();
})();

(function revealOnScroll() {
  const targets = document.querySelectorAll('[data-fx], .reveal, .reveal-left, .reveal-right, .reveal-line, .reveal-stagger');
  if (!targets.length) return;

  const reduceMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  if (reduceMotion || !('IntersectionObserver' in window)) {
    targets.forEach((t) => t.classList.add('in-view'));
    document.body.classList.add('is-loaded');
    return;
  }

  const io = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('in-view');
        io.unobserve(entry.target);
      }
    });
  }, { threshold: 0.12, rootMargin: '0px 0px -40px 0px' });

  targets.forEach((t) => io.observe(t));

  /* Lưới an toàn: nếu IO vì lý do nào đó không bắn (vd. phần tử từng bị
     clip kín diện tích), tự gắn .in-view cho mục đang nằm trong viewport.
     Chạy throttled theo scroll + 1 lần quét sau load. */
  let pending = new Set(targets);
  const origUnobserve = io.unobserve.bind(io);
  function sweepVisible() {
    if (!pending.size) {
      window.removeEventListener('scroll', scheduleSweep);
      return;
    }
    const vh = window.innerHeight;
    pending.forEach((t) => {
      if (t.classList.contains('in-view')) { pending.delete(t); return; }
      const r = t.getBoundingClientRect();
      if (r.bottom > 0 && r.top < vh && r.right > 0 && r.left < window.innerWidth) {
        const area = Math.max(0, Math.min(r.bottom, vh) - Math.max(r.top, 0)) *
                     Math.max(0, Math.min(r.right, window.innerWidth) - Math.max(r.left, 0));
        const total = Math.max(1, r.width * r.height);
        if (area / total > 0.1) {
          t.classList.add('in-view');
          pending.delete(t);
          origUnobserve(t);
        }
      }
    });
  }
  let sweepQueued = false;
  function scheduleSweep() {
    if (sweepQueued) return;
    sweepQueued = true;
    requestAnimationFrame(() => { sweepQueued = false; sweepVisible(); });
  }
  window.addEventListener('scroll', scheduleSweep, { passive: true });
  window.addEventListener('resize', scheduleSweep);
  setTimeout(sweepVisible, 1200);
  setTimeout(sweepVisible, 3500);
})();

/* ─── Showcase 3D tilt theo chuột (chỉ khi đã hiện, bỏ qua cảm ứng) ─── */
(function showcaseTilt() {
  const mockup = document.querySelector('.desktop-mockup');
  const win = document.querySelector('.desktop-window');
  if (!mockup || !win) return;
  const reduceMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduceMotion || window.matchMedia('(hover: none)').matches) return;

  let raf = 0;
  mockup.style.perspective = '1400px';
  function onMove(e) {
    const r = mockup.getBoundingClientRect();
    const px = (e.clientX - r.left) / r.width - 0.5;
    const py = (e.clientY - r.top) / r.height - 0.5;
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => {
      win.style.transform =
        'rotateY(' + (px * 5).toFixed(2) + 'deg) rotateX(' + (-py * 5).toFixed(2) + 'deg)';
      win.style.transition = 'transform .12s linear';
    });
  }
  function onLeave() {
    cancelAnimationFrame(raf);
    win.style.transition = 'transform .6s cubic-bezier(.16,1,.3,1)';
    win.style.transform = '';
  }
  mockup.addEventListener('pointermove', onMove);
  mockup.addEventListener('pointerleave', onLeave);
})();


/* ─── NEW: Nav-pill scroll state ─────────────────────────────────
   Toggles `.is-scrolled` on #navPill once the page has scrolled past
   a small threshold, so the pill can pick up a stronger background/
   shadow once content starts moving under it. */
(function navScrollState() {
  const pill = document.getElementById('navPill');
  if (!pill) return;

  const THRESHOLD = 8;
  let ticking = false;

  function apply() {
    pill.classList.toggle('is-scrolled', window.scrollY > THRESHOLD);
    ticking = false;
  }
  function onScroll() {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(apply);
  }

  window.addEventListener('scroll', onScroll, { passive: true });
  apply();
})();


/* ─── NEW: Ambient orb parallax ───────────────────────────────────
   Nudges the three .bg-orb blobs vertically as the page scrolls, at
   different speeds, so the backdrop feels alive rather than static.
   Runs alongside the CSS drift animation (transform gets added, not
   replaced) via a wrapping translateY on each orb's own transform. */
(function orbParallax() {
  const orbs = document.querySelectorAll('.bg-orb');
  if (!orbs.length) return;

  const reduceMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduceMotion) return;

  const speeds = [0.06, -0.04, 0.09];
  let ticking = false;

  function apply() {
    const y = window.scrollY;
    orbs.forEach((orb, i) => {
      orb.style.setProperty('--parallax-y', (y * (speeds[i % speeds.length])).toFixed(1) + 'px');
    });
    ticking = false;
  }
  function onScroll() {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(apply);
  }

  window.addEventListener('scroll', onScroll, { passive: true });
  apply();
})();


/* ─── NEW: Case-card spotlight ──────────────────────────────────
   Tracks pointer position over each `.case` card and writes it into
   `--mx` / `--my` (percentage) custom properties, which `.case::before`
   uses to render a soft light-following glow. Pointer-based (works for
   touch too via pointermove) and skipped entirely on reduced-motion. */
(function caseSpotlight() {
  const cards = document.querySelectorAll('.case');
  if (!cards.length) return;

  const reduceMotion = window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduceMotion) return;

  cards.forEach((card) => {
    card.addEventListener('pointermove', (e) => {
      const rect = card.getBoundingClientRect();
      const mx = ((e.clientX - rect.left) / rect.width) * 100;
      const my = ((e.clientY - rect.top) / rect.height) * 100;
      card.style.setProperty('--mx', mx + '%');
      card.style.setProperty('--my', my + '%');
    });
    card.addEventListener('pointerleave', () => {
      card.style.setProperty('--mx', '50%');
      card.style.setProperty('--my', '50%');
    });
  });
})();


/* ─── NEW: Desktop Showcase View Switcher & Actions ──────────── */
(function desktopShowcase() {
  const modeVideoBtn = document.getElementById('scModeVideo');
  const modeSimBtn = document.getElementById('scModeSim');
  const videoView = document.getElementById('dwVideoView');
  const simView = document.getElementById('dwSimView');
  const videoEl = videoView ? videoView.querySelector('video') : null;
  const playPauseBtn = document.getElementById('videoPlayPauseBtn');

  if (modeVideoBtn && modeSimBtn && videoView && simView) {
    modeVideoBtn.addEventListener('click', () => {
      modeVideoBtn.classList.add('is-active');
      modeSimBtn.classList.remove('is-active');
      videoView.style.display = 'flex';
      simView.style.display = 'none';
      if (videoEl && videoEl.paused) {
        videoEl.play().catch(() => {});
        if (playPauseBtn) playPauseBtn.textContent = '⏸ Pause';
      }
    });

    modeSimBtn.addEventListener('click', () => {
      modeSimBtn.classList.add('is-active');
      modeVideoBtn.classList.remove('is-active');
      videoView.style.display = 'none';
      simView.style.display = 'grid';
      if (videoEl && !videoEl.paused) {
        videoEl.pause();
      }
    });
  }

  if (playPauseBtn && videoEl) {
    playPauseBtn.addEventListener('click', () => {
      if (videoEl.paused) {
        videoEl.play().catch(() => {});
        playPauseBtn.textContent = '⏸ Pause';
        playPauseBtn.setAttribute('aria-label', 'Pause video');
      } else {
        videoEl.pause();
        playPauseBtn.textContent = '▶ Play';
        playPauseBtn.setAttribute('aria-label', 'Play video');
      }
    });
  }

  const navBtns = document.querySelectorAll('.ds-nav-item');
  const panels = {
    chat: document.getElementById('dwChatView'),
    inbox: document.getElementById('dwInboxView'),
    audit: document.getElementById('dwAuditView')
  };
  if (!navBtns.length) return;

  function switchTab(targetTab) {
    navBtns.forEach((btn) => {
      const isTarget = btn.getAttribute('data-view') === targetTab;
      btn.classList.toggle('is-active', isTarget);
    });

    Object.keys(panels).forEach((key) => {
      const p = panels[key];
      if (p) p.classList.toggle('is-active', key === targetTab);
    });
  }

  navBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      const tab = btn.getAttribute('data-view');
      if (tab && panels[tab]) switchTab(tab);
    });
  });

  const approveBtn = document.getElementById('demoApproveBtn');
  const denyBtn = document.getElementById('demoDenyBtn');
  const inboxBadge = document.querySelector('.ds-badge');

  if (approveBtn) {
    approveBtn.addEventListener('click', () => {
      approveBtn.textContent = '✓ Approved & Written';
      approveBtn.style.background = 'var(--ok)';
      approveBtn.style.color = '#000';
      if (denyBtn) denyBtn.style.display = 'none';
      if (inboxBadge) {
        inboxBadge.textContent = '0';
        inboxBadge.style.background = 'color-mix(in oklab, var(--ok) 20%, var(--bg-1))';
        inboxBadge.style.color = 'var(--ok)';
      }
      setTimeout(() => switchTab('audit'), 800);
    });
  }

  if (denyBtn) {
    denyBtn.addEventListener('click', () => {
      denyBtn.textContent = '✗ Revoked';
      denyBtn.style.color = 'var(--err)';
      if (approveBtn) approveBtn.style.display = 'none';
      if (inboxBadge) {
        inboxBadge.textContent = '0';
      }
      setTimeout(() => switchTab('chat'), 800);
    });
  }
})();


/* ─── NEW: FAQ Accordion ───────────────────────────────────────── */
(function faqAccordion() {
  const items = document.querySelectorAll('.faq-item');
  if (!items.length) return;

  items.forEach((item) => {
    const trigger = item.querySelector('.faq-trigger');
    const body = item.querySelector('.faq-body');
    if (!trigger || !body) return;

    trigger.addEventListener('click', () => {
      const isOpen = item.classList.contains('is-open');

      // Close other items for accordion behavior
      items.forEach((other) => {
        if (other !== item && other.classList.contains('is-open')) {
          other.classList.remove('is-open');
          const otherTrigger = other.querySelector('.faq-trigger');
          const otherBody = other.querySelector('.faq-body');
          if (otherTrigger) otherTrigger.setAttribute('aria-expanded', 'false');
          if (otherBody) otherBody.hidden = true;
        }
      });

      if (isOpen) {
        item.classList.remove('is-open');
        trigger.setAttribute('aria-expanded', 'false');
        body.hidden = true;
      } else {
        item.classList.add('is-open');
        trigger.setAttribute('aria-expanded', 'true');
        body.hidden = false;
      }
    });
  });
})();


/* ─── Platform Auto-Detection for Download Section ──────── */
function getDetectedOS() {
  const ua = (navigator.userAgent || '').toLowerCase();
  const platform = (navigator.platform || '').toLowerCase();
  const uadPlatform = (navigator.userAgentData && navigator.userAgentData.platform)
    ? navigator.userAgentData.platform.toLowerCase()
    : '';

  if (uadPlatform.includes('win') || ua.includes('win') || platform.includes('win')) {
    return 'win';
  } else if (uadPlatform.includes('linux') || ua.includes('linux') || ua.includes('x11') || platform.includes('linux')) {
    return 'linux';
  } else if (uadPlatform.includes('mac') || ua.includes('mac') || platform.includes('mac')) {
    return 'mac';
  }
  return 'mac';
}

window.updateDownloadUIForDevice = function() {
  const detectedOs = getDetectedOS();

  // Remove any stale is-detected classes or badges first
  document.querySelectorAll('.download-card').forEach((card) => {
    card.classList.remove('is-detected');
    const existingBadge = card.querySelector('.dl-rec-badge');
    if (existingBadge) existingBadge.remove();
  });

  const detectedCard = document.querySelector(`.download-card[data-os="${detectedOs}"]`);
  if (detectedCard) {
    detectedCard.classList.add('is-detected');
    // Reorder: place the detected OS card first so user sees it right away
    const grid = detectedCard.parentElement;
    if (grid && grid.firstElementChild !== detectedCard) {
      grid.prepend(detectedCard);
    }

    // Add badge to detected card
    const badgeWrap = detectedCard.querySelector('.dl-badge-wrap');
    if (badgeWrap) {
      const badge = document.createElement('span');
      badge.className = 'dl-rec-badge mono';
      badge.setAttribute('data-i18n', 'dl.rec');
      const lang = document.documentElement.getAttribute('data-lang') || 'en';
      badge.textContent = lang === 'vi' ? 'Phù hợp với thiết bị của bạn' : 'Detected for your device';
      badgeWrap.appendChild(badge);
    }

    // Dynamically customize Hero Download CTA for detected device
    const heroBtn = document.getElementById('heroDownloadBtn');
    if (heroBtn) {
      const lang = document.documentElement.getAttribute('data-lang') || 'en';
      const osNames = {
        mac: 'macOS (.dmg)',
        win: 'Windows (.exe)',
        linux: 'Linux (.deb)'
      };
      const osLinks = {
        mac: '/downloads/stable/Astra-macos-arm64.dmg',
        win: '/downloads/stable/Astra-windows-setup.exe',
        linux: '/downloads/stable/Astra-linux-amd64.deb'
      };
      const osTarget = osNames[detectedOs] || 'Desktop';
      const btnText = heroBtn.querySelector('.hero-btn-text') || heroBtn.querySelector('[data-i18n="cta.download"]');
      if (btnText) {
        btnText.className = 'hero-btn-text';
        btnText.textContent = lang === 'vi' ? `Tải về cho ${osTarget}` : `Download for ${osTarget}`;
      }
      if (osLinks[detectedOs]) {
        heroBtn.setAttribute('href', osLinks[detectedOs]);
      }
    }
  }
};

(function initDownloadDetection() {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', window.updateDownloadUIForDevice);
  } else {
    window.updateDownloadUIForDevice();
  }
})();

