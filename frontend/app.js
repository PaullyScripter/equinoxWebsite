if (window.__equinox_app_loaded) {
  console.warn("app.js loaded twice - skipping init");
  throw new Error("Duplicate app.js load prevented");
}
window.__equinox_app_loaded = true;

let cachedMe = null;
let mePromise = null;

function getCookie(name) {
  return document.cookie
    .split("; ")
    .find(r => r.startsWith(name + "="))
    ?.split("=")[1];
}

function getCsrfToken() {
  return decodeURIComponent(getCookie("csrf_token") || "");
}

async function adminFetch(path, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("X-CSRF-Token", getCsrfToken());

  return fetch(path, {
    credentials: "include",
    ...options,
    headers,
  });
}

async function getMe() {
  if (cachedMe) return cachedMe;
  if (!mePromise) {
    mePromise = fetch("/api/me", { credentials: "include" })
      .then(async (res) => {
        if (res.status === 429) {
          let body = null;
          try { body = await res.json(); } catch {}
          const retry = Number(body?.detail?.retry_after || res.headers.get("Retry-After") || 0);
          return { rate_limited: true, retry_after: retry };
        }
        if (!res.ok) return null;
        const data = await res.json();
        try { sessionStorage.setItem("equinox_user", JSON.stringify(data)); } catch {}
        return data;
      })
      .catch(function () {
        try {
          const stored = sessionStorage.getItem("equinox_user");
          return stored ? JSON.parse(stored) : null;
        } catch {
          return null;
        }
      })
      .then((data) => (cachedMe = data));
  }
  return mePromise;
}


function formatSeconds(s) {
  s = Math.max(0, Math.ceil(s));
  const m = Math.floor(s / 60);
  const r = s % 60;
  if (m <= 0) return `${r}s`;
  return `${m}m ${r}s`;
}

if (window.Typed && document.querySelector(".typedText")) {
  new Typed(".typedText", {
    strings: ["utilities","tools","apps","commands","systems"],
    loop: true, typeSpeed: 100, backSpeed: 80, backDelay: 2000
  });
}

window.addEventListener("scroll", function () {
  const navBar = document.querySelector(".navigation_bar");
  if (!navBar) return;
  if (window.pageYOffset > 10) navBar.classList.add("navigation_bar-colored");
  else navBar.classList.remove("navigation_bar-colored");
});

(function () {
  const selector = ".features_img img, .second_feature_image, .third_feature_image, .fourth_feature_image";
  const thumbs = document.querySelectorAll(selector);

  thumbs.forEach(img => {
    img.style.cursor = "zoom-in";
    img.addEventListener("click", () => openViewer(img));
  });

  // Compute translation to center + fitted scale
  function fitTransform(rect, padding = 48, maxScaleFactor = 0.85) {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const maxW = vw - padding * 2;
    const maxH = vh - padding * 2;

    const fit = Math.min(maxW / rect.width, maxH / rect.height);
    const scale = Math.min(fit, maxScaleFactor);

    const targetX = vw / 2;
    const targetY = vh / 2;
    const currentX = rect.left + rect.width / 2;
    const currentY = rect.top + rect.height / 2;

    return {
      tx: targetX - currentX,
      ty: targetY - currentY,
      scale
    };
  }

  function openViewer(img) {
    const rect = img.getBoundingClientRect();

    // Backdrop
    const backdrop = document.createElement("div");
    backdrop.className = "image-backdrop"; // needs CSS from earlier step
    document.body.appendChild(backdrop);

    // Clone placed over original
    const clone = img.cloneNode(true);
    clone.classList.add("zoom-clone"); // needs CSS from earlier step
    Object.assign(clone.style, {
      top: rect.top + "px",
      left: rect.left + "px",
      width: rect.width + "px",
      height: rect.height + "px",
      transform: "translate(0,0) scale(1)"
    });
    document.body.appendChild(clone);

    // Close button (SVG X), perfectly centered inside the circle
    const closeBtn = document.createElement("button");
    closeBtn.className = "close-modal-fab"; // needs CSS from earlier step
    closeBtn.setAttribute("aria-label", "Close");
    closeBtn.innerHTML = `
      <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
        <path d="M18 6L6 18M6 6l12 12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
      </svg>`;
    document.body.appendChild(closeBtn);

    // Lock page scroll while open
    document.body.classList.add("no-scroll");

    // Animate in
    requestAnimationFrame(() => {
      const { tx, ty, scale } = fitTransform(rect, 48, 2); // tweak 0.85 if you want smaller/larger
      backdrop.classList.add("show");
      clone.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
      clone.style.cursor = "zoom-out";
    });

    // Keep it responsive
    const onResize = () => {
      const { tx, ty, scale } = fitTransform(rect, 48, 0.85);
      clone.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
    };
    window.addEventListener("resize", onResize);

    // Close & cleanup
    const onClose = () => {
      clone.style.transform = "translate(0,0) scale(1)";
      backdrop.classList.remove("show");
      document.body.classList.remove("no-scroll");
      setTimeout(() => {
        clone.remove();
        backdrop.remove();
        closeBtn.remove();
        window.removeEventListener("keydown", onKey);
        window.removeEventListener("resize", onResize);
      }, 320);
    };

    backdrop.addEventListener("click", onClose);
    closeBtn.addEventListener("click", onClose);
    clone.addEventListener("click", onClose); // click enlarged image to close
    const onKey = (e) => (e.key === "Escape") && onClose();
    window.addEventListener("keydown", onKey);
  }
})();

(function () {
  const titleEl = document.querySelector(".homepage_title");
  if (!titleEl) return;

  // Respect reduced motion, but no sessionStorage gating anymore
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduceMotion) return;

  const originalHTML = titleEl.innerHTML;
  const targetText   = titleEl.textContent.trim();

  const pool = (() => {
    const latin = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
    const digits = "0123456789";
    const symbols = `!@#$%^&*()_+-=[]{}|;:'",.<>/?~•◈◇◆★☆✦✧`;
    const greekLower = "αβγδεζηθικλμνξοπρστυφχψω";
    const greekUpper = "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ";
    return latin + digits + symbols + greekLower + greekUpper;
  })();

  const duration = 1200; // ms
  const fps = 60;
  const steps = Math.max(12, Math.floor((duration / 1000) * fps));
  const length = targetText.length;

  let frame = 0;
  const scramble = () => {
    frame++;
    const lockCount = Math.floor((frame / steps) * length);
    let out = "";

    for (let i = 0; i < length; i++) {
      const ch = targetText[i];
      if (/\s/.test(ch) || i < lockCount) {
        out += ch;
      } else {
        const greek = "αβγδεζηθικλμνξοπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ";
        out += Math.random() < 0.12
          ? greek[(Math.random() * greek.length) | 0]
          : pool[(Math.random() * pool.length) | 0];
      }
    }

    titleEl.textContent = out;

    if (frame < steps) {
      setTimeout(scramble, (1000 / fps) + (4 + Math.random() * 10));
    } else {
      titleEl.innerHTML = originalHTML; // restore styled HTML
    }
  };

  requestAnimationFrame(scramble);
})();

// ===============================
// Splash screen: scramble animation with loading bar, logo reveal, scale+fade exit
// ===============================
(function(){
  const splash = document.getElementById('splash');
  const splashTitle = document.getElementById('splash-title');
  const splashBar = document.getElementById('splash-bar');
  const splashLogo = document.getElementById('splash-logo');
  const target = document.querySelector('.homepage_title');
  if (!splash || !splashTitle || !target) return;

  if (sessionStorage.getItem('splashShown') === '1') {
    splash.remove();
    document.body.classList.remove('no-scroll');
    document.documentElement.classList.remove('no-scroll');
    return;
  }
  sessionStorage.setItem('splashShown', '1');
  document.body.classList.add('no-scroll');
  document.documentElement.classList.add('no-scroll');

  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduceMotion) {
    splash.remove();
    document.body.classList.remove('no-scroll');
    document.documentElement.classList.remove('no-scroll');
    window.dispatchEvent(new CustomEvent("equinox:splash-ended"));
    return;
  }

  const px = (v) => {
    const dpr = window.devicePixelRatio || 1;
    return Math.round(v * dpr) / dpr;
  };

  function copyTypography(from, to){
    const cs = getComputedStyle(from);
    const props = [
      "font", "fontFamily", "fontSize", "fontWeight", "fontStyle", "fontStretch", "fontVariant",
      "lineHeight", "letterSpacing", "wordSpacing", "textTransform", "textRendering", "fontKerning",
      "fontFeatureSettings", "fontVariantLigatures",
      "textShadow", "textDecoration", "textDecorationThickness",
      "textAlign", "direction"
    ];
    props.forEach(p => to.style[p] = cs[p]);
  }

  function measureTarget(){
    const range = document.createRange();
    range.selectNodeContents(target);
    const rects = range.getClientRects();
    const rWhole = target.getBoundingClientRect();
    const r = Array.from(rects).find(rr => rr.width > 0 && rr.height > 0) || rWhole;
    return r;
  }

  function syncSplashPosition(){
    const r = measureTarget();
    const vw = window.innerWidth;
    const vh = window.innerHeight;

    // Fallback: center if target is off-screen or below fold
    if (r.left < 0 || r.top < 0 || r.top > vh || r.left > vw) {
      splashTitle.style.left = "50%";
      splashTitle.style.top  = "50%";
      splashTitle.style.transform = "translate(-50%, -50%)";
      splashTitle.style.width = "auto";
      splashTitle.style.height = "auto";
      splashTitle.style.transformOrigin = "center center";
    } else {
      copyTypography(target, splashTitle);
      splashTitle.style.position = "fixed";
      splashTitle.style.whiteSpace = "nowrap";
      splashTitle.style.margin = "0";
      splashTitle.style.padding = "0";
      splashTitle.style.zIndex = "2";
      splashTitle.style.transform = "none";
      splashTitle.style.left = px(r.left) + "px";
      splashTitle.style.top  = px(r.top)  + "px";
      splashTitle.style.width = px(r.width) + "px";
      splashTitle.style.height = px(r.height) + "px";
      const cs = getComputedStyle(target);
      splashTitle.style.transformOrigin = cs.transformOrigin || "left top";
    }
    splashTitle.style.color = "#fff";
    splashTitle.style.zIndex = "2";
  }

  // Debounced ResizeObserver + resize (rAF throttled)
  let ro;
  let syncPending = false;
  function syncRAF() {
    if (syncPending) return;
    syncPending = true;
    requestAnimationFrame(() => {
      syncSplashPosition();
      syncPending = false;
    });
  }

  function bindObservers(){
    ro = new ResizeObserver(syncRAF);
    ro.observe(document.documentElement);
    ro.observe(document.body);
    ro.observe(target);
    window.addEventListener("resize", syncRAF);
  }

  function unbindObservers(){
    if (ro) ro.disconnect();
    window.removeEventListener("resize", syncRAF);
  }

  const pool = (() => {
    const latin = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
    const digits = "0123456789";
    const symbols = "!@#$%^&*()_+-=[]{}|;:'\",.<>/?~•◈◇◆★☆✦✧";
    const greekLower = "αβγδεζηθικλμνξοπρστυφχψω";
    const greekUpper = "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ";
    return latin + digits + symbols + greekLower + greekUpper;
  })();

  const targetText = "Σquinϕx";
  const duration = 1200;
  const fps = 60;
  const steps = Math.max(12, Math.floor((duration / 1000) * fps));
  const length = targetText.length;
  const frameInterval = 1000 / fps;

  function endSplash() {
    unbindObservers();
    requestAnimationFrame(() => {
      splash.classList.add('splash-hidden');
      document.body.classList.remove('no-scroll');
      document.documentElement.classList.remove('no-scroll');
      window.dispatchEvent(new CustomEvent("equinox:splash-ended"));
      setTimeout(() => splash.remove(), 650);
    });
  }

  function showLogo() {
    if (!splashLogo) return;
    // Clone the nav logo SVG into the splash-logo container
    const logoSrc = document.querySelector('.logo svg');
    if (logoSrc) {
      splashLogo.innerHTML = '';
      splashLogo.appendChild(logoSrc.cloneNode(true));
    }
    splashLogo.classList.add('visible');
  }

  (document.fonts?.ready || Promise.resolve()).then(() => {
    requestAnimationFrame(() => {
      syncSplashPosition();
      // Override to left-align during scramble so changing characters don't shift
      // the visual center (fixed-width container prevents horizontal jitter)
      splashTitle.style.textAlign = "left";
      bindObservers();

      let frame = 0;
      let lastTime = performance.now();

      function scramble(now) {
        const elapsed = now - lastTime;
        if (elapsed >= frameInterval) {
          lastTime = now - (elapsed % frameInterval);
          frame++;
          const lockCount = Math.floor((frame / steps) * length);
          let out = "";

          for (let i = 0; i < length; i++) {
            const ch = targetText[i];
            if (/\s/.test(ch) || i < lockCount) {
              out += ch;
            } else {
              const greek = "αβγδεζηθικλμνξοπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ";
              out += Math.random() < 0.15
                ? greek[(Math.random() * greek.length) | 0]
                : pool[(Math.random() * pool.length) | 0];
            }
          }

          splashTitle.textContent = out;

          if (splashBar) {
            const progress = Math.min(100, (frame / steps) * 100);
            splashBar.style.width = progress + "%";
          }
        }

        if (frame < steps) {
          requestAnimationFrame(scramble);
        } else {
          unbindObservers();
          // Restore center alignment - same font, same container, exact position match
          splashTitle.style.textAlign = "center";
          splashTitle.textContent = targetText;
          if (splashBar) splashBar.style.width = "100%";
          showLogo();
          setTimeout(endSplash, 600);
        }
      }

      requestAnimationFrame(scramble);
    });
  });
})();

(function () {
  const btn = document.getElementById('contact-toggle');
  const menu = document.getElementById('contact-menu');

  if (!btn || !menu) return;

  function closeMenu() {
    menu.classList.remove('open');
    btn.setAttribute('aria-expanded', 'false');
    menu.setAttribute('aria-hidden', 'true');
  }

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const willOpen = !menu.classList.contains('open');
    document.querySelectorAll('.contact-dropdown.open').forEach(el => el.classList.remove('open'));
    if (willOpen) {
      menu.classList.add('open');
      btn.setAttribute('aria-expanded', 'true');
      menu.setAttribute('aria-hidden', 'false');
    } else {
      closeMenu();
    }
  });

  document.addEventListener('click', (e) => {
    if (!menu.contains(e.target) && e.target !== btn) closeMenu();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeMenu();
  });
})();

// ===============================
// Discord Login/Profile Button Logic
// ===============================
const BACKEND_URL = "http://45.131.65.107:25777";

(function () {
  const profileBtn = document.getElementById("profile-btn");
  const loginLabel = document.getElementById("login-label");
  const userContent = document.getElementById("user-content");
  const userAvatar = document.getElementById("user-avatar");
  const userNameEl = document.getElementById("user-name");
  const logoutBtn = document.getElementById("logout-btn");
  const dropdown = document.getElementById("profile-dropdown");

  if (!profileBtn) return;
  let loginInProgress = false;

  // Toggle dropdown on click when logged in
  profileBtn.addEventListener("click", () => {
      // 1. Prevent action if already loading
      if (loginInProgress) return;
      
      if (userContent.classList.contains("nav-hidden")) {
          // 2. Lock the state
          loginInProgress = true;
          
          // 3. Visual feedback: Change text and look
          const originalText = profileBtn.innerText;
          profileBtn.innerText = "Connecting...";
          profileBtn.style.opacity = "0.6";
          profileBtn.style.cursor = "not-allowed";
          profileBtn.style.pointerEvents = "none"; // Physically stops clicks

          const next = encodeURIComponent(window.location.pathname);
          window.location.href = `/auth/discord/login?next=${next}`;
          
          // 4. Safety Timeout: Re-enable after 10s if redirect fails
          setTimeout(() => {
              loginInProgress = false;
              profileBtn.innerText = originalText;
              profileBtn.style.opacity = "1";
              profileBtn.style.cursor = "pointer";
              profileBtn.style.pointerEvents = "auto";
          }, 10000);

      } else {
          dropdown.classList.toggle("nav-hidden");
      }
  });

  // Logout
  logoutBtn.addEventListener("click", async () => {


    dropdown.classList.add("nav-hidden");
    await fetch("/auth/logout", { method: "POST", credentials: "include" });
    cachedMe = null;
    mePromise = null;
    showLoggedOut();
  });

  function showLoggedIn(user) {
    loginLabel.classList.add("hidden");
    userContent.classList.remove("nav-hidden");

    // avatar (prefer backend-generated URL)
    userAvatar.src =
      user.avatar_url ||
      (user.avatar
        ? `https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png`
        : "");

    userAvatar.alt = `${user.username || "User"}'s avatar`;
    userAvatar.style.display = "block";

    function truncateName(name) {
      return name.length > 12 ? name.slice(0, 12) + "…" : name;
    }

    userNameEl.textContent = truncateName(
      user.global_name || user.username || "User"
    );

    // fallback if avatar fails to load
    userAvatar.onerror = () => {
      userAvatar.src = "images/default-avatar.png";
    };

    dropdown.classList.add("nav-hidden");
  }


  function showLoggedOut() {
    // show "Login" text again
    loginLabel.classList.remove("hidden");

    // hide the user content + dropdown
    userContent.classList.add("nav-hidden");
    dropdown.classList.add("nav-hidden");
  }
  async function init() {
    try {
      const user = await getMe(); // user object OR null

      if (!user) {
        console.debug("Not logged in; /api/me returned null");
        showLoggedOut();
        return;
      }

      // If your getMe() returns { rate_limited: true, retry_after: ... } you can handle it here:
      if (user.rate_limited) {
        const human = formatSeconds(Number(user.retry_after || 0));
        showPageError(`Discord rate-limited login. Try again after ${human}.`);
        showLoggedOut();
        return;
      }

      console.debug("User from /api/me:", user);
      showLoggedIn(user);
    } catch (e) {
      console.error("init() failed:", e);
      showLoggedOut();
    }
  }



  // 🧠 Avoid layout jank during the splash animation:
  // if the splash is present & visible, wait for it to finish.
  const splashEl = document.getElementById("splash");
  if (splashEl && !splashEl.classList.contains("splash-hidden")) {
    const onSplashEnd = () => {
      window.removeEventListener("equinox:splash-ended", onSplashEnd);
      init();
    };
    window.addEventListener("equinox:splash-ended", onSplashEnd);
  } else {
    init();
  }

})();





// ---------- Shared helper for this page logic ----------
// ---------- Shared helper for this page logic ----------
async function fetchAuthedJSON(path) {
  try {
    const res = await fetch(path, {
      credentials: "include",
    });

    if (!res.ok) return null;
    return await res.json();
  } catch (err) {
    console.error("fetchAuthedJSON error:", err);
    return null;
  }
}


// ---------- Premium page: show status + heading ----------
async function initPremiumStatusUI() {
  const banner = document.getElementById("premium-status-banner");

  if (!banner) return;

  const user = await getMe();
  if (!user) {
    return;
  }

  const premium = await fetchAuthedJSON("/api/premium");
  if (!premium || !premium.premium) {
    return;
  }

  const displayName = user.global_name || user.username || "there";

  if (banner) {
    banner.textContent = `You're a premium user, ${displayName}! Thank you for supporting Equinox.`;
    banner.style.display = "block";
  }

  const cards = document.querySelector(".premium-cards");
  if (cards) {
    cards.style.setProperty("display", "flex", "important");
  }
  document.querySelectorAll(".premium-box").forEach(function (card) {
    card.style.setProperty("display", "flex", "important");
  });
}

// ---------- Thank-you page: protect + fill avatar/name ----------
async function initThankYouPage() {
  const pathname = window.location.pathname.toLowerCase();
  if (!pathname.endsWith("thankyou.html")) return;

  const user = await getMe();
  if (!user) {
    window.location.href = "premium.html";
    return;
  }

  const premium = await fetchAuthedJSON("/api/premium"); // ✅ you forgot this
  if (!premium || !premium.premium) {
    window.location.href = "premium.html";
    return;
  }

  const avatarEl = document.getElementById("ty-avatar");
  const nameEl = document.getElementById("ty-name");
  const tierEl = document.getElementById("ty-tier");

  if (avatarEl && user.avatar_url) {
    avatarEl.src = user.avatar_url; // ✅ use the backend-provided URL
    avatarEl.alt = `${user.username}'s avatar`;
  }

  if (nameEl) {
    nameEl.textContent = user.username || "Premium user";
  }

  if (tierEl && premium.tier) {
    const niceTier = premium.tier.charAt(0).toUpperCase() + premium.tier.slice(1);
    tierEl.textContent = `Plan: ${niceTier}`;
  }
}


async function initSubscriptionPage() {
  const pathname = window.location.pathname.toLowerCase();
  const isSubscription =
    pathname.endsWith("subscription.html") ||
    pathname.endsWith("/subscription") ||
    pathname.endsWith("/subscription/");

  if (!isSubscription) return;


  const avatarEl = document.getElementById("sub-avatar");
  const nameEl = document.getElementById("sub-name");
  const statusEl = document.getElementById("sub-status");
  const detailsEl = document.getElementById("sub-details");

  // Must be logged in
  const user = await getMe();
  if (!user) {
    // not logged in → send them to premium page (or login)
    window.location.href = "premium.html";
    return;
  }

  // Fill avatar + name
  if (avatarEl) avatarEl.src = user.avatar_url || "";
  if (nameEl) nameEl.textContent = user.global_name || user.username || "User";

  // Load subscription details
  const sub = await fetchAuthedJSON("/api/subscription");
  if (!sub) {
    if (statusEl) statusEl.textContent = "Couldn’t load subscription info.";
    return;
  }

  if (!sub.premium) {
    if (statusEl) statusEl.textContent = "Status: Not Premium";
    if (detailsEl) detailsEl.innerHTML = `<p style="margin:0.4rem 0;">No active subscription.</p>`;
    return;
  }

  const niceTier = sub.tier ? sub.tier.charAt(0).toUpperCase() + sub.tier.slice(1) : "Premium";
  if (statusEl) statusEl.textContent = `Status: Premium (${niceTier})`;

  const started = sub.started_at ? new Date(sub.started_at).toLocaleString() : "-";
  const ends = sub.expires_at ? new Date(sub.expires_at).toLocaleString() : "-";
  const codeUsed = sub.code_used || "Hidden";

  if (detailsEl) {
    detailsEl.innerHTML = `
      <div style="display:grid; gap:0.45rem;">
        <div><strong>Plan:</strong> ${niceTier}</div>
        <div><strong>Started:</strong> ${started}</div>
        <div><strong>Ends:</strong> ${ends}</div>
        <div><strong>Code used:</strong> ${codeUsed}</div>
      </div>
    `;
  }
}


// ---------- Hook into page load ----------
document.addEventListener("DOMContentLoaded", () => {
  const pathname = window.location.pathname.toLowerCase();

  const isPremium =
    pathname.endsWith("premium.html") ||
    pathname.endsWith("/premium") ||
    pathname.endsWith("/premium/");

  const isThankYou =
    pathname.endsWith("thankyou.html") ||
    pathname.endsWith("/thankyou") ||
    pathname.endsWith("/thankyou/");

  const isSubscription =
    pathname.endsWith("subscription.html") ||
    pathname.endsWith("/subscription") ||
    pathname.endsWith("/subscription/");

  if (isPremium) initPremiumStatusUI();
  if (isThankYou) initThankYouPage();
  if (isSubscription) initSubscriptionPage();
});

function getRedeemCodeFormatted() {
  const boxes = Array.from(document.querySelectorAll(".code-box"));

  const raw = boxes
    .map(b => (b.value || ""))
    .join("")
    .replace(/[^A-Za-z0-9]/g, "");   // keep case

  if (raw.length !== 16) return null;
  return raw.match(/.{1,4}/g).join("-");
}

document.addEventListener("DOMContentLoaded", () => {
  setupRedeemCodeBoxes();
});

function showRedeemToast(type, text, ms = 3000) {
  const msg = document.getElementById("redeem-msg");
  if (!msg) return;

  // reset classes
  msg.classList.remove("info", "success", "error", "show");

  // set type + text
  msg.classList.add(type);
  msg.textContent = text;

  // trigger animation
  requestAnimationFrame(() => msg.classList.add("show"));

  // auto hide
  window.clearTimeout(showRedeemToast._t);
  showRedeemToast._t = window.setTimeout(() => {
    msg.classList.remove("show");
  }, ms);
}


document.addEventListener("DOMContentLoaded", () => {
  const btn = document.getElementById("redeem-btn");
  const msg = document.getElementById("redeem-msg");
  if (!btn || !msg) return;


  btn.addEventListener("click", async () => {
    const code = getRedeemCodeFormatted();
    if (!code) {
      showRedeemToast("info", "Please enter all 16 characters.");
      return;
    }

    try {
      const res = await fetch(`/api/redeem`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });

      if (res.ok) {
        await res.json();
        showRedeemToast("success", "Redeemed successfully!");
        document.querySelectorAll(".code-box").forEach(b => b.value = "");
        return;
      }

      // ✅ LOCKED (server says too many attempts)
      if (res.status === 429) {
        let data = null;
        try { data = await res.json(); } catch {}

        // FastAPI often returns { detail: { retry_after: ... } }
        const detail = data?.detail || data || {};
        const retryAfter = Number(detail.retry_after ?? 0);

        if (retryAfter > 0) {
          showRedeemToast(
            "error",
            `Too many attempts. Try again in ${formatSeconds(retryAfter)}.`,
            4000
          );
          return;
        }

        showRedeemToast("error", "Too many attempts. Please try again later.", 4000);
        return;
      }

      // default error (invalid vs used stays hidden)
      showRedeemToast("error", "Redeem failed. Please try another code.");
    } catch (e) {
      showRedeemToast("error", "Redeem failed. Please try again.");
    }
  });


});

function showPageError(text, ms = 3000) {
  const el = document.getElementById("page-toast");
  if (!el) return;

  el.textContent = text;
  el.classList.add("show");

  clearTimeout(showPageError._t);
  showPageError._t = setTimeout(() => el.classList.remove("show"), ms);
}


document.addEventListener("DOMContentLoaded", () => {
  const modal = document.getElementById("redeem_section_modal");
  const openBtn = document.getElementById("redeem_section_button");
  const closeBtn = modal?.querySelector(".close");

  if (!modal || !openBtn) return;

  openBtn.addEventListener("click", async () => {
    const user = await getMe();

    if (!user) {
      showPageError("You must log in first to redeem a code.");
      return;
    }

    modal.classList.add("show");
    document.body.classList.add("modal-open");
  });

  const closeModal = () => {
    modal.classList.remove("show");
    document.body.classList.remove("modal-open");
  };

  closeBtn?.addEventListener("click", closeModal);

  window.addEventListener("click", (event) => {
    if (event.target === modal) closeModal();
  });
});



function setupRedeemCodeBoxes() {
  const boxes = Array.from(document.querySelectorAll(".code-box"));
  if (!boxes.length) return;

  const isValidChar = (ch) => /^[a-z0-9]$/i.test(ch);

  // Auto-advance, only allow alphanumerics, uppercase display
  boxes.forEach((box, idx) => {
    box.addEventListener("input", () => {
      let v = (box.value || "").slice(-1);
      if (!isValidChar(v)) v = "";
      box.value = v;

      if (box.value && idx < boxes.length - 1) {
        boxes[idx + 1].focus();
      }
    });

    box.addEventListener("keydown", (e) => {
      if (e.key === "Backspace" && !box.value && idx > 0) {
        boxes[idx - 1].focus();
      }
      if (e.key === "ArrowLeft" && idx > 0) boxes[idx - 1].focus();
      if (e.key === "ArrowRight" && idx < boxes.length - 1) boxes[idx + 1].focus();
    });

    // Paste support: user can paste with or without dashes
    box.addEventListener("paste", (e) => {
      e.preventDefault();
      const paste = (e.clipboardData || window.clipboardData).getData("text");
      const cleaned = (paste || "").replace(/[^A-Za-z0-9]/g, "").slice(0, 16);
      if (!cleaned) return;

      for (let i = 0; i < boxes.length; i++) {
        boxes[i].value = cleaned[i] || "";
      }
      const next = Math.min(cleaned.length, boxes.length - 1);
      boxes[next].focus();
    });
  });
}


const DEV_DISCORD_ID = "857932717681147954";

function isDevUser(user) {
  if (!user) return false;
  if (user.is_dev) return true;
  if (String(user.id) === DEV_DISCORD_ID) return true;
  return false;
}

async function initDevLink() {
  const devLink = document.getElementById("dev-link");
  if (!devLink) return;

  const user = await getMe();
  if (!isDevUser(user)) {
    devLink.classList.add("developer-hidden");
    return;
  }

  devLink.classList.remove("developer-hidden");
}

async function protectDeveloperPage() {
  const pathname = window.location.pathname.toLowerCase();
  const isDevPage = pathname.endsWith("developer.html") || pathname.endsWith("/developer");
  if (!isDevPage) return;

  const user = await getMe();
  if (!user) return;
  if (!isDevUser(user)) {
    window.location.replace("premium.html");
    return;
  }
}



document.addEventListener("DOMContentLoaded", () => {
  initDevLink();
  protectDeveloperPage();
});

// ── Typed.js cursor color ──
(function () {
  if (typeof Typed === "undefined") return;
  const style = document.createElement("style");
  style.textContent = ".typed-cursor { color: var(--yellow-accent); }";
  document.head.appendChild(style);
})();

// ── "Try Me" smooth scroll ──
(function () {
  const btn = document.querySelector('.homepage-invite-button[href="#features"]');
  if (!btn) return;
  btn.addEventListener("click", (e) => {
    const target = document.getElementById("features");
    if (target) {
      e.preventDefault();
      target.scrollIntoView({ behavior: "smooth" });
    }
  });
})();

// ── Feature image parallax ──
(function () {
  const cards = document.querySelectorAll(".features_card");
  if (!cards.length) return;
  let ticking = false;
  window.addEventListener("scroll", () => {
    if (!ticking) {
      requestAnimationFrame(() => {
        cards.forEach(card => {
          const imgs = card.querySelectorAll(".features_img img, .second_feature_image, .third_feature_image, .fourth_feature_image");
          const rect = card.getBoundingClientRect();
          const center = rect.top + rect.height / 2;
          const viewCenter = window.innerHeight / 2;
          const offset = (center - viewCenter) * 0.05;
          imgs.forEach(img => { img.style.transform = `translateY(${offset}px)`; });
        });
        ticking = false;
      });
      ticking = true;
    }
  }, { passive: true });
})();

// ── Page transitions ──
(function () {
  if (sessionStorage.getItem("splashShown") !== "1") return;
  document.body.style.opacity = "0";
  requestAnimationFrame(() => {
    document.body.style.opacity = "1";
    document.body.classList.add("page-transition-in");
  });
  document.querySelectorAll("a").forEach(a => {
    if (a.hostname === window.location.hostname && !a.hasAttribute("target")) {
      a.addEventListener("click", function (e) {
        const href = this.getAttribute("href");
        if (!href || href.startsWith("#")) return;
        e.preventDefault();
        document.body.classList.add("page-transition-out");
        setTimeout(() => { window.location.href = href; }, 180);
      });
    }
  });
})();

// ── Redeem code shake on error ──
(function () {
  const redeemBtn = document.getElementById("redeem-btn");
  if (!redeemBtn) return;
  const origClick = redeemBtn.onclick;
  redeemBtn.addEventListener("click", async function (e) {
    const code = getRedeemCodeFormatted();
    if (!code) return;
    try {
      const res = await fetch("/api/redeem", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      if (!res.ok) {
        document.querySelectorAll(".code-box").forEach(b => {
          b.classList.add("shake");
          setTimeout(() => b.classList.remove("shake"), 400);
        });
      }
    } catch {}
  });
})();

// ── FAQ accordion ──
(function () {
  document.querySelectorAll(".faq-question").forEach(btn => {
    btn.addEventListener("click", () => {
      const item = btn.parentElement;
      const wasOpen = item.classList.contains("open");
      document.querySelectorAll(".faq-item.open").forEach(i => i.classList.remove("open"));
      if (!wasOpen) item.classList.add("open");
    });
  });
})();

// ── Canvas particle background ──
(function () {
  const canvas = document.getElementById("particle-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  let w, h, particles = [];
  function resize() {
    const parent = canvas.parentElement;
    w = parent.offsetWidth;
    h = parent.offsetHeight;
    canvas.width = w;
    canvas.height = h;
    particles = Array.from({ length: 40 }, () => ({
      x: Math.random() * w, y: Math.random() * h,
      vx: (Math.random() - 0.5) * 0.3, vy: (Math.random() - 0.5) * 0.3,
      r: Math.random() * 1.2 + 0.3, o: Math.random() * 0.25
    }));
  }
  resize();
  window.addEventListener("resize", resize);
  function draw() {
    ctx.clearRect(0, 0, w, h);
    particles.forEach(p => {
      p.x += p.vx;
      p.y += p.vy;
      if (p.x < 0) p.x = w;
      if (p.x > w) p.x = 0;
      if (p.y < 0) p.y = h;
      if (p.y > h) p.y = 0;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(255,255,255,${p.o})`;
      ctx.fill();
    });
    requestAnimationFrame(draw);
  }
  draw();
})();

// ── Social proof number counter ──
(function () {
  const stats = document.querySelectorAll(".social-proof .stat");
  if (!stats.length) return;
  let counted = false;
  window.addEventListener("scroll", () => {
    if (counted) return;
    const el = stats[0].closest(".social-proof");
    if (!el) return;
    const rect = el.getBoundingClientRect();
    if (rect.top < window.innerHeight * 0.8) {
      counted = true;
      stats.forEach(stat => {
        const target = parseFloat(stat.getAttribute("data-count"));
        const suffix = stat.getAttribute("data-suffix") || "";
        const decimals = target % 1 !== 0 ? 1 : 0;
        const duration = 1500;
        let start;
        function update(ts) {
          if (!start) start = ts;
          const progress = Math.min(1, (ts - start) / duration);
          const eased = 1 - Math.pow(1 - progress, 3);
          stat.textContent = (eased * target).toFixed(decimals) + suffix;
          if (progress < 1) requestAnimationFrame(update);
        }
        requestAnimationFrame(update);
      });
    }
  }, { passive: true });
})();

// ── Mobile bottom nav active state ──
(function () {
  const nav = document.getElementById("mobile-nav");
  if (!nav) return;
  const path = window.location.pathname.toLowerCase();
  nav.querySelectorAll("a").forEach(a => {
    if (a.getAttribute("href") && path.includes(a.getAttribute("href").replace(".html", ""))) {
      a.classList.add("active");
    }
  });
})();

// ── Premium card 3D tilt (mouse only) ──
(function () {
  if (!window.matchMedia("(hover: hover)").matches) return;
  document.querySelectorAll(".premium-box").forEach(card => {
    card.addEventListener("mousemove", (e) => {
      const rect = card.getBoundingClientRect();
      const x = (e.clientX - rect.left) / rect.width - 0.5;
      const y = (e.clientY - rect.top) / rect.height - 0.5;
      card.style.transform = `scale(1.09) rotateY(${x * 15}deg) rotateX(${-y * 15}deg)`;
    });
    card.addEventListener("mouseleave", () => { card.style.transform = ""; });
  });
})();

(function () {
  const banner = document.getElementById("cookie-consent");
  const btn = document.getElementById("cookie-accept");
  if (!banner || !btn) return;

  function showBanner() {
    if (!localStorage.getItem("cookies-accepted")) {
      banner.hidden = false;
    }
  }

  const splashEl = document.getElementById("splash");
  if (splashEl && !splashEl.classList.contains("splash-hidden")) {
    window.addEventListener("equinox:splash-ended", () => {
      showBanner();
    }, { once: true });
  } else {
    showBanner();
  }

  btn.addEventListener("click", () => {
    localStorage.setItem("cookies-accepted", "1");
    banner.hidden = true;
  });
})();
