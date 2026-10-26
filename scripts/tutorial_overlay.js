// Presentation layer injected ONLY while recording tutorials: animated cursor, click ripples,
// spotlight on the element being explained and step captions with a progress bar.
(() => {
  if (window.__fp) return;
  const css = `
  #fp-cursor{position:fixed;left:0;top:0;width:26px;height:26px;z-index:2147483647;pointer-events:none;
    transform:translate(720px,450px);transition:transform .9s cubic-bezier(.22,.9,.3,1);filter:drop-shadow(0 4px 8px rgba(15,20,36,.35))}
  .fp-ripple{position:fixed;width:16px;height:16px;margin:-8px 0 0 -8px;border-radius:50%;background:rgba(79,70,229,.35);
    border:2px solid #4F46E5;z-index:2147483646;pointer-events:none;animation:fpr .6s ease-out forwards}
  @keyframes fpr{to{transform:scale(4.2);opacity:0}}
  #fp-spot{position:fixed;z-index:2147483640;pointer-events:none;border-radius:14px;opacity:0;
    box-shadow:0 0 0 4px rgba(79,70,229,.9),0 0 0 9999px rgba(15,20,36,.42);transition:all .55s cubic-bezier(.22,.9,.3,1)}
  #fp-cap{position:fixed;left:50%;bottom:28px;transform:translate(-50%,24px);opacity:0;z-index:2147483645;pointer-events:none;
    width:min(760px,86vw);background:rgba(27,36,64,.94);color:#fff;border-radius:16px;padding:16px 20px 14px;
    font:15px/1.45 Inter,-apple-system,"Segoe UI",Roboto,Arial,sans-serif;box-shadow:0 18px 48px rgba(15,20,36,.35);
    transition:all .45s cubic-bezier(.22,.9,.3,1);backdrop-filter:blur(6px)}
  #fp-cap.on{transform:translate(-50%,0);opacity:1}
  #fp-cap.top{bottom:auto;top:76px}
  #fp-cap .top{display:flex;align-items:center;gap:10px;margin-bottom:4px}
  #fp-cap .n{background:#4F46E5;border-radius:99px;padding:2px 10px;font-size:12px;font-weight:700;letter-spacing:.02em}
  #fp-cap .t{font-weight:700;font-size:17px}#fp-cap .d{color:#D6DAEA}
  #fp-cap .bar{height:3px;background:rgba(255,255,255,.18);border-radius:99px;margin-top:12px;overflow:hidden}
  #fp-cap .bar i{display:block;height:100%;background:#818CF8;transition:width .6s ease}
`;
  const style = document.createElement("style"); style.textContent = css;
  const cursor = document.createElement("div"); cursor.id = "fp-cursor";
  cursor.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26"><path d="M4 2l15 11-6.5 1.2L9 21z" fill="#fff" stroke="#1B2440" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  const spot = document.createElement("div"); spot.id = "fp-spot";
  const cap = document.createElement("div"); cap.id = "fp-cap";
  cap.innerHTML = '<div class="top"><span class="n"></span><span class="t"></span></div><div class="d"></div><div class="bar"><i></i></div>';
  const mount = () => { document.head.append(style); document.body.append(spot, cap, cursor); };
  if (document.body) mount(); else document.addEventListener("DOMContentLoaded", mount);
  let pos = JSON.parse(sessionStorage.getItem("fp-pos") || "[720,450]");
  cursor.style.transition = "none"; cursor.style.transform = `translate(${pos[0]}px,${pos[1]}px)`;
  requestAnimationFrame(() => { cursor.style.transition = ""; });
  const center = el => { const r = el.getBoundingClientRect(); return [r.left + Math.min(r.width / 2, 120), r.top + r.height / 2]; };
  window.__fp = {
    moveXY(x, y) { pos = [x, y]; sessionStorage.setItem("fp-pos", JSON.stringify(pos)); cursor.style.transform = `translate(${x}px,${y}px)`; },
    spotBox(x, y, w, h, pad = 10) { Object.assign(spot.style, {left: x - pad + "px", top: y - pad + "px", width: w + 2 * pad + "px", height: h + 2 * pad + "px", opacity: 1}); },
    move(sel) { const el = document.querySelector(sel); if (!el) return false; el.scrollIntoView({block: "center", behavior: "instant"});
      const [x, y] = center(el); pos = [x, y]; sessionStorage.setItem("fp-pos", JSON.stringify(pos));
      cursor.style.transform = `translate(${x}px,${y}px)`; return true; },
    ripple() { const r = document.createElement("div"); r.className = "fp-ripple"; r.style.left = pos[0] + "px"; r.style.top = pos[1] + "px";
      document.body.append(r); setTimeout(() => r.remove(), 700); },
    spot(sel, pad = 10) { const el = document.querySelector(sel); if (!el) { spot.style.opacity = 0; return; }
      el.scrollIntoView({block: "center", behavior: "instant"}); const r = el.getBoundingClientRect();
      Object.assign(spot.style, {left: r.left - pad + "px", top: r.top - pad + "px", width: r.width + 2 * pad + "px",
        height: r.height + 2 * pad + "px", opacity: 1}); },
    unspot() { spot.style.opacity = 0; },
    caption(n, total, title, text) { cap.querySelector(".n").textContent = `Step ${n} of ${total}`;
      cap.querySelector(".t").textContent = title; cap.querySelector(".d").textContent = text;
      cap.querySelector(".bar i").style.width = `${100 * n / total}%`; cap.classList.add("on"); },
    hide() { cap.classList.remove("on"); },
    place(where) { cap.classList.toggle("top", where === "top"); },
  };
})();
