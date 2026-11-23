/* WYGRANA 🏆 — pokaz jak MEGA JACKPOT w kasynie po przejściu leada na „Wygrany” (wspólny plik CRM PL i CRM LT/DE).
 *
 *   crmCelebrate({id, company, value, currency}, {self: true|false}) → Promise (kończy się po zamknięciu pokazu)
 *
 * Każda wygrana to wielki krok dla firmy, więc każda dostaje pełną oprawę jak główna wygrana w kasynie:
 *   1. ALARM (0–1,9 s): szkolny dzwonek „brrrr”, migające koguty, trzęsienie ziemi całej strony.
 *   2. WYGRANA! (jak „big win”): huk, wybuch monet ze środka, fontanna monet, licznik kwoty z tykaniem, światełka.
 *   3. JERZY ZAMYKA TEMAT! (jak „mega win”, z imieniem osoby): mocniejsze trzęsienie, nowy dźwięk, więcej monet.
 *   4. TEMAT ZAMKNIĘTY! (jak jackpot): armata pieniędzy, fajerwerki, talerze, fanfara, „ka-ching” na końcu licznika.
 *   Klimat i grafika jak w kasynie, ale nazwy nasze i śmieszne — bez kasynowych napisów 1:1.
 *   5. Finał: tańczący robot z Chin, samoloty z banerami, smok i lampiony, oklaski, liczby z CRM (która to wygrana
 *      w miesiącu, suma miesiąca, REKORD MIESIĄCA) i przycisk „Dawaj następnego! 🚀”.
 * Wszystko lokalnie (CSS, SVG, canvas, dźwięk syntezowany w Web Audio) — bez plików i CDN.
 * Zamykanie: przycisk, Esc / Enter albo samo po ~17 s. Dźwięk: 🔊 w rogu (zapamiętany w przeglądarce).
 * Ograniczone animacje w systemie (prefers-reduced-motion) → spokojna karta z tymi samymi liczbami, bez dźwięku.
 */
(function () {
  'use strict';

  const ALARM_MS = 1900;      // dzwonek i trzęsienie ziemi przed wybuchem
  const TIER_MS = 1450;       // odstęp między poziomami WYGRANA → MEGA → JACKPOT
  const COUNT_MS = 3 * TIER_MS + 300;

  const CSS = `
.wg { position: fixed; inset: 0; z-index: 9990; overflow: hidden; color: #fff; font-family: 'Inter Var', Inter, system-ui, sans-serif;
      animation: wg-fade-in .2s ease both; user-select: none; }
.wg.wg-out { animation: wg-fade-out .45s ease forwards; }
.wg-bg { position: absolute; inset: 0; background: radial-gradient(circle at 50% 45%, rgba(120, 20, 20, .35), rgba(9, 11, 16, .55) 70%);
         transition: background .4s; }
.wg.wg-go .wg-bg { background: radial-gradient(circle at 50% 45%, rgba(38, 86, 180, .9), rgba(8, 9, 14, .97) 62%); backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px); }
.wg.wg-jack .wg-bg { background: radial-gradient(circle at 50% 45%, rgba(150, 82, 12, .94), rgba(58, 12, 42, .95) 42%, rgba(8, 9, 14, .98) 72%);
                     backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px); }
.wg-rays { position: absolute; left: 50%; top: 45%; width: 220vmax; height: 220vmax; margin: -110vmax 0 0 -110vmax; opacity: 0;
           background: repeating-conic-gradient(from 0deg, rgba(255, 209, 102, .2) 0deg 7deg, transparent 7deg 18deg);
           -webkit-mask: radial-gradient(circle, #000 8%, transparent 46%); mask: radial-gradient(circle, #000 8%, transparent 46%);
           animation: wg-spin 18s linear infinite; transition: opacity .5s; }
.wg.wg-go .wg-rays { opacity: 1; }
.wg.wg-jack .wg-rays { animation-duration: 7s; }
.wg-flash { position: absolute; inset: 0; background: radial-gradient(circle at 50% 45%, #fffbe8, rgba(255, 209, 102, .7) 30%, transparent 72%);
            animation: wg-flash .8s ease-out forwards; pointer-events: none; z-index: 7; }
.wg-cv { position: absolute; inset: 0; pointer-events: none; z-index: 5; }
.wg-stage { position: absolute; inset: 0; }  /* trzęsie się scena, a nie tło z rozmyciem (inaczej przeglądarka gubi warstwę) */
.wg-shake { animation: wg-shake .55s cubic-bezier(.36, .07, .19, .97) both; }
.wg-quake { animation: wg-quake .09s linear infinite; }

/* ALARM: koguty i dzwonek */
.wg-beacon { position: absolute; top: 50%; width: 160vmax; height: 160vmax; margin-top: -80vmax; pointer-events: none; mix-blend-mode: screen;
             background: conic-gradient(from 0deg, rgba(255, 40, 40, .55) 0deg 22deg, transparent 22deg 180deg, rgba(255, 190, 40, .5) 180deg 202deg, transparent 202deg);
             animation: wg-spin .7s linear infinite; }
.wg-beacon.wg-b1 { left: -80vmax; } .wg-beacon.wg-b2 { right: -80vmax; animation-direction: reverse; }
.wg-alarm { position: absolute; left: 50%; top: 45%; transform: translate(-50%, -50%); text-align: center; z-index: 6; }
.wg-bell { font-size: clamp(110px, 18vw, 220px); line-height: 1; display: inline-block; animation: wg-ring .07s linear infinite alternate;
           filter: drop-shadow(0 0 40px rgba(255, 200, 60, .9)); }
.wg-alarm-txt { font: 800 clamp(20px, 3.2vw, 40px) 'Poppins', sans-serif; letter-spacing: .12em; margin-top: 8px; color: #ffe08a;
                text-shadow: 0 0 20px rgba(255, 60, 60, .9); animation: wg-blink-txt .22s steps(2) infinite; }
.wg.wg-go .wg-alarm, .wg.wg-go .wg-beacon { display: none; }

/* światełka dookoła ekranu jak w automacie */
.wg-bulbs { position: absolute; inset: 0; pointer-events: none; z-index: 4; opacity: 0; transition: opacity .3s; }
.wg.wg-go .wg-bulbs { opacity: 1; }
.wg-bulbs i { position: absolute; background-image: radial-gradient(circle, #fff7d1 0 3px, #ffd166 4px, rgba(255, 209, 102, 0) 8px),
                                                    radial-gradient(circle, #ff5a5a 0 3px, rgba(255, 90, 90, .6) 4px, rgba(255, 90, 90, 0) 8px);
              background-size: 32px 32px; animation: wg-chase .32s steps(2) infinite; filter: drop-shadow(0 0 6px #ffd166); }
.wg-bulbs i:nth-child(1), .wg-bulbs i:nth-child(2) { left: 0; right: 0; height: 32px; background-position: 0 0, 16px 0; }
.wg-bulbs i:nth-child(1) { top: 0; } .wg-bulbs i:nth-child(2) { bottom: 0; }
.wg-bulbs i:nth-child(3), .wg-bulbs i:nth-child(4) { top: 32px; bottom: 32px; width: 32px; background-position: 0 0, 0 16px; animation-name: wg-chase-v; }
.wg-bulbs i:nth-child(3) { left: 0; } .wg-bulbs i:nth-child(4) { right: 0; }

.wg-center { position: absolute; left: 50%; top: 47%; transform: translate(-50%, -50%); width: min(1100px, 94vw); text-align: center; z-index: 6;
             visibility: hidden; }
.wg.wg-go .wg-center { visibility: visible; }
.wg-kicker { display: inline-block; font-weight: 800; letter-spacing: .14em; text-transform: uppercase; font-size: clamp(12px, 1.6vw, 16px);
             padding: 7px 16px; border-radius: 999px; background: rgba(255, 209, 102, .14); border: 1px solid rgba(255, 209, 102, .45); color: #ffe08a; }
.wg-title { font-family: 'Poppins', sans-serif; font-weight: 700; font-size: clamp(46px, 10vw, 150px); line-height: 1; margin: 12px 0 2px; white-space: nowrap;
            filter: drop-shadow(0 4px 0 #7a3d00) drop-shadow(0 10px 30px rgba(255, 170, 30, .6)); }
.wg-title span { display: inline-block; -webkit-text-stroke: 2px #8a4b00;
                 background: linear-gradient(100deg, #ffe07a 0%, #ffd166 30%, #fffbe6 42%, #ffc531 54%, #f08c00 80%, #ffe9a8 100%);
                 background-size: 250% 100%; -webkit-background-clip: text; background-clip: text; color: transparent;
                 animation: wg-drop .5s cubic-bezier(.2, 1.6, .4, 1) both, wg-shine 1.4s linear infinite; }
.wg-title.wg-mega span { background: linear-gradient(100deg, #ffb3d9 0%, #ff5fa2 30%, #fff 42%, #ff8ac1 54%, #c2187a 80%, #ffd0e8 100%); background-size: 250% 100%;
                         -webkit-background-clip: text; background-clip: text; -webkit-text-stroke-color: #5e0b3a; }
.wg-title.wg-jackpot span { -webkit-text-stroke: 3px #6b2c00; animation: wg-slam .55s cubic-bezier(.2, 1.4, .4, 1) both, wg-shine .9s linear infinite, wg-jiggle .5s .6s ease-in-out infinite; }
.wg-title.wg-slam span { animation: wg-slam .5s cubic-bezier(.2, 1.4, .4, 1) both, wg-shine 1.2s linear infinite; }
.wg-company { font-family: 'Poppins', sans-serif; font-weight: 700; font-size: clamp(19px, 2.6vw, 32px); text-shadow: 0 4px 24px rgba(0, 0, 0, .6);
              animation: wg-rise .5s .3s ease both; }
.wg-money { display: inline-block; font-family: 'Poppins', sans-serif; font-weight: 700; font-size: clamp(42px, 8vw, 104px); line-height: 1.05; margin-top: 4px;
            padding: 2px 26px; border-radius: 22px; color: #7dffb0; font-variant-numeric: tabular-nums;
            background: linear-gradient(180deg, rgba(0, 0, 0, .45), rgba(0, 0, 0, .25)); border: 2px solid rgba(255, 209, 102, .55);
            box-shadow: 0 0 40px rgba(255, 209, 102, .35), inset 0 0 30px rgba(125, 255, 176, .15);
            text-shadow: 0 0 30px rgba(125, 255, 176, .7); animation: wg-pop .5s .25s cubic-bezier(.2, 1.5, .4, 1) both; }
.wg-money.wg-bump { animation: wg-bump .35s ease; }
.wg-hype { min-height: 1.5em; font-size: clamp(16px, 2.1vw, 24px); font-weight: 600; margin-top: 12px; color: #e9eefc; }
.wg-hype span { display: inline-block; animation: wg-hype-in .45s ease both; }
.wg-chips { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; margin-top: 12px; min-height: 34px; }
.wg-chip { padding: 7px 14px; border-radius: 999px; font-size: clamp(12.5px, 1.4vw, 15px); font-weight: 700; background: rgba(255, 255, 255, .1);
           border: 1px solid rgba(255, 255, 255, .22); backdrop-filter: blur(6px); animation: wg-pop .5s cubic-bezier(.2, 1.5, .4, 1) both; }
.wg-chip.wg-gold { background: linear-gradient(135deg, #ffd166, #f0a020); color: #2a1a00; border: 0; box-shadow: 0 0 30px rgba(255, 196, 60, .7);
                   animation: wg-pop .5s cubic-bezier(.2, 1.5, .4, 1) both, wg-pulse 1s .5s ease-in-out infinite; }
.wg-next { margin-top: 18px; font: 800 clamp(15px, 1.7vw, 19px) 'Poppins', sans-serif; color: #241600; cursor: pointer; border: 0; padding: 14px 30px;
           border-radius: 999px; background: linear-gradient(135deg, #fff3c4, #ffd166 40%, #f0a020); visibility: hidden;
           box-shadow: 0 10px 40px rgba(255, 190, 50, .55), inset 0 -3px 0 rgba(0, 0, 0, .15); }
.wg-next.wg-show { visibility: visible; animation: wg-pop .5s cubic-bezier(.2, 1.5, .4, 1) both; }
.wg-next:hover { transform: translateY(-2px) scale(1.03); }
.wg-mute { position: absolute; top: 42px; right: 42px; z-index: 9; width: 44px; height: 44px; border-radius: 50%; cursor: pointer; font-size: 20px;
           background: rgba(0, 0, 0, .45); border: 1px solid rgba(255, 255, 255, .3); color: #fff; }

/* robot z Chin */
.wg-robot { position: absolute; left: max(36px, 4vw); bottom: 28px; width: clamp(120px, 16vw, 230px); z-index: 6; display: none; }
.wg-robot.wg-show { display: block; animation: wg-robot-in .9s cubic-bezier(.2, 1.3, .4, 1) both; }
.wg-robot svg { width: 100%; height: auto; display: block; filter: drop-shadow(0 0 30px rgba(79, 216, 255, .35)); animation: wg-bob .55s .9s ease-in-out infinite alternate; }
.wg-bot-head { transform-box: fill-box; transform-origin: 50% 100%; animation: wg-head .55s .9s ease-in-out infinite alternate; }
.wg-arm { transform-box: fill-box; transform-origin: 50% 6%; }
.wg-arm-l { animation: wg-arm-l .42s .8s ease-in-out infinite alternate; transform: rotate(150deg); }
.wg-arm-r { animation: wg-arm-r .42s 1s ease-in-out infinite alternate; transform: rotate(-150deg); }
.wg-eye { animation: wg-blink 2.6s 2s infinite; transform-box: fill-box; transform-origin: center; }
.wg-antenna { animation: wg-led .25s infinite alternate; }
.wg-bubble { position: absolute; left: 70%; bottom: 84%; white-space: nowrap; padding: 10px 14px; border-radius: 16px 16px 16px 4px; background: #fff;
             color: #141824; font-weight: 800; font-size: clamp(13px, 1.4vw, 17px); box-shadow: 0 10px 30px rgba(0, 0, 0, .35);
             animation: wg-pop .45s .9s cubic-bezier(.2, 1.6, .4, 1) both; }

/* samoloty z banerami */
.wg-plane { position: absolute; left: 0; display: flex; align-items: center; z-index: 6; will-change: transform; }
.wg-plane.wg-r { animation: wg-fly-r var(--dur, 6.5s) linear both; }
.wg-plane.wg-l { flex-direction: row-reverse; animation: wg-fly-l var(--dur, 7s) linear both; }
.wg-plane.wg-l .wg-plane-svg { transform: scaleX(-1); }
.wg-plane-in { display: flex; align-items: center; animation: wg-plane-bob 1.1s ease-in-out infinite alternate; }
.wg-l .wg-plane-in { flex-direction: row-reverse; }
.wg-plane-svg { width: clamp(80px, 9vw, 130px); height: auto; filter: drop-shadow(0 6px 10px rgba(0, 0, 0, .35)); }
.wg-rope { width: 46px; height: 2px; background: rgba(255, 255, 255, .7); }
.wg-banner { font: 800 clamp(14px, 1.8vw, 22px) 'Poppins', sans-serif; white-space: nowrap; color: #1d1300; padding: 8px 18px;
             background: linear-gradient(180deg, #fff3c4, #ffd166); border-radius: 4px; box-shadow: 0 8px 20px rgba(0, 0, 0, .3);
             transform-origin: 100% 50%; animation: wg-flag .5s ease-in-out infinite alternate; }
.wg-l .wg-banner { transform-origin: 0 50%; }

/* Chiny: lampiony i smok */
.wg-lantern { position: absolute; font-size: clamp(34px, 4.5vw, 62px); z-index: 5; transform-origin: 50% 0;
              animation: wg-lantern-in 1s cubic-bezier(.2, 1.4, .4, 1) both, wg-swing 1.8s 1s ease-in-out infinite alternate; }
.wg-lantern::before { content: ""; position: absolute; left: 50%; top: -80px; height: 80px; width: 2px; background: rgba(255, 209, 102, .7); }
.wg-dragon { position: absolute; top: 60%; left: 0; font-size: clamp(70px, 10vw, 140px); z-index: 5; filter: drop-shadow(0 0 30px rgba(255, 80, 60, .6));
             animation: wg-dragon 5.2s ease-in-out both; }
.wg-clap { position: absolute; bottom: -60px; font-size: clamp(26px, 3vw, 42px); z-index: 7; animation: wg-clap var(--t, 2.4s) ease-out forwards; pointer-events: none; }

/* strona pod spodem trzęsie się razem z pokazem */
.wg-page-quake { animation: wg-quake .09s linear infinite !important; }
.wg-page-shake { animation: wg-shake .55s cubic-bezier(.36, .07, .19, .97) both !important; }

/* spokojna wersja */
.wg-calm .wg-center { top: 50%; visibility: visible; }
.wg-calm .wg-alarm, .wg-calm .wg-beacon { display: none; }
.wg-calm .wg-next { visibility: visible; }

@keyframes wg-fade-in { from { opacity: 0; } }
@keyframes wg-fade-out { to { opacity: 0; transform: scale(1.04); } }
@keyframes wg-spin { to { transform: rotate(360deg); } }
@keyframes wg-flash { from { opacity: 1; } to { opacity: 0; } }
@keyframes wg-shake { 10%, 90% { transform: translate(-3px, 2px) rotate(-.3deg); } 20%, 80% { transform: translate(7px, -4px) rotate(.4deg); }
                      30%, 50%, 70% { transform: translate(-12px, 6px) rotate(-.6deg); } 40%, 60% { transform: translate(12px, -6px) rotate(.6deg); } }
@keyframes wg-quake { 0% { transform: translate(0, 0); } 25% { transform: translate(-6px, 4px) rotate(-.4deg); } 50% { transform: translate(5px, -5px) rotate(.3deg); }
                      75% { transform: translate(-4px, -3px) rotate(.2deg); } 100% { transform: translate(6px, 5px) rotate(-.3deg); } }
@keyframes wg-ring { from { transform: rotate(-22deg) scale(1.02); } to { transform: rotate(22deg) scale(.98); } }
@keyframes wg-blink-txt { 50% { opacity: .25; } }
@keyframes wg-chase { to { background-position: 16px 0, 0 0; } }
@keyframes wg-chase-v { to { background-position: 0 16px, 0 0; } }
@keyframes wg-drop { 0% { transform: translateY(-120vh) rotate(-25deg) scale(1.6); opacity: 0; } 60% { opacity: 1; } 100% { transform: none; } }
@keyframes wg-slam { 0% { transform: scale(3.4); opacity: 0; filter: blur(8px); } 55% { transform: scale(.9); opacity: 1; filter: none; } 100% { transform: none; } }
@keyframes wg-shine { from { background-position: 120% 0; } to { background-position: -130% 0; } }
@keyframes wg-jiggle { 0%, 100% { transform: translateY(0) rotate(0); } 25% { transform: translateY(-7px) rotate(-3deg); } 75% { transform: translateY(4px) rotate(3deg); } }
@keyframes wg-pop { from { transform: scale(.2); opacity: 0; } }
@keyframes wg-rise { from { transform: translateY(26px); opacity: 0; } }
@keyframes wg-bump { 40% { transform: scale(1.16); box-shadow: 0 0 80px rgba(255, 209, 102, .9); } }
@keyframes wg-hype-in { from { transform: translateY(14px) scale(.96); opacity: 0; } }
@keyframes wg-pulse { 50% { transform: scale(1.07); box-shadow: 0 0 46px rgba(255, 196, 60, .95); } }
@keyframes wg-robot-in { from { transform: translateY(130%); } }
@keyframes wg-bob { to { transform: translateY(-10px); } }
@keyframes wg-head { from { transform: rotate(-7deg); } to { transform: rotate(7deg); } }
@keyframes wg-arm-l { from { transform: rotate(140deg); } to { transform: rotate(195deg); } }
@keyframes wg-arm-r { from { transform: rotate(-140deg); } to { transform: rotate(-195deg); } }
@keyframes wg-blink { 0%, 92%, 100% { transform: scaleY(1); } 95% { transform: scaleY(.1); } }
@keyframes wg-led { to { fill: #ffd166; } }
@keyframes wg-fly-r { from { transform: translateX(-100%); } to { transform: translateX(100vw); } }
@keyframes wg-fly-l { from { transform: translateX(100vw); } to { transform: translateX(-100%); } }
@keyframes wg-plane-bob { to { transform: translateY(10px) rotate(1.5deg); } }
@keyframes wg-flag { from { transform: skewY(-2deg); } to { transform: skewY(2deg); } }
@keyframes wg-lantern-in { from { transform: translateY(-180px); } }
@keyframes wg-swing { from { transform: rotate(-9deg); } to { transform: rotate(9deg); } }
@keyframes wg-dragon { 0% { transform: translate(105vw, 0) rotate(-6deg); } 25% { transform: translate(70vw, -60px) rotate(8deg); }
                       50% { transform: translate(40vw, 20px) rotate(-8deg); } 75% { transform: translate(12vw, -50px) rotate(8deg); }
                       100% { transform: translate(-30vw, 0) rotate(-6deg); } }
@keyframes wg-clap { 0% { transform: translateY(0) scale(.6); opacity: 0; } 15% { opacity: 1; } 100% { transform: translateY(-75vh) scale(1.2) rotate(var(--r, 10deg)); opacity: 0; } }
@media (max-width: 700px) { .wg-center { top: 42%; } .wg-dragon { top: 68%; } .wg-mute { top: 38px; right: 38px; } .wg-title { -webkit-text-stroke-width: 1px; } }
`;

  const ROBOT = `<svg viewBox="0 0 160 250" aria-hidden="true">
  <defs>
    <linearGradient id="wgMetal" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f6f8fc"/><stop offset="1" stop-color="#b3bed0"/></linearGradient>
    <linearGradient id="wgDark" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2b3242"/><stop offset="1" stop-color="#10141c"/></linearGradient>
    <radialGradient id="wgEye"><stop offset="0" stop-color="#e6fdff"/><stop offset=".55" stop-color="#4fd8ff"/><stop offset="1" stop-color="#1f8fd6"/></radialGradient>
  </defs>
  <rect x="56" y="166" width="18" height="62" rx="9" fill="url(#wgDark)"/>
  <rect x="86" y="166" width="18" height="62" rx="9" fill="url(#wgDark)"/>
  <rect x="47" y="224" width="31" height="13" rx="6.5" fill="#0b0e14"/>
  <rect x="82" y="224" width="31" height="13" rx="6.5" fill="#0b0e14"/>
  <g class="wg-arm wg-arm-l"><rect x="22" y="94" width="18" height="62" rx="9" fill="url(#wgMetal)"/><circle cx="31" cy="160" r="10" fill="url(#wgDark)"/></g>
  <g class="wg-arm wg-arm-r"><rect x="120" y="94" width="18" height="62" rx="9" fill="url(#wgMetal)"/><circle cx="129" cy="160" r="10" fill="url(#wgDark)"/></g>
  <rect x="43" y="90" width="74" height="84" rx="22" fill="url(#wgMetal)"/>
  <rect x="57" y="106" width="46" height="32" rx="10" fill="url(#wgDark)"/>
  <text x="80" y="128" text-anchor="middle" font-family="Poppins, sans-serif" font-weight="700" font-size="17" fill="#4fd8ff">33</text>
  <circle cx="80" cy="155" r="5" fill="#ffd166"/>
  <rect x="70" y="80" width="20" height="13" rx="4" fill="#7c8799"/>
  <g class="wg-bot-head">
    <rect x="41" y="16" width="78" height="66" rx="26" fill="url(#wgMetal)"/>
    <rect x="49" y="32" width="62" height="31" rx="15.5" fill="url(#wgDark)"/>
    <circle class="wg-eye" cx="66" cy="47" r="8.5" fill="url(#wgEye)"/>
    <circle class="wg-eye" cx="94" cy="47" r="8.5" fill="url(#wgEye)"/>
    <path d="M69 57 Q80 64 91 57" stroke="#4fd8ff" stroke-width="3" fill="none" stroke-linecap="round"/>
    <line x1="80" y1="16" x2="80" y2="5" stroke="#b3bed0" stroke-width="3"/>
    <circle class="wg-antenna" cx="80" cy="5" r="5" fill="#ff5a5a"/>
  </g>
</svg>`;

  const PLANE = `<svg class="wg-plane-svg" viewBox="0 0 130 52" aria-hidden="true">
  <path d="M10 25 L100 21 Q124 21 124 27 Q124 33 100 33 L10 31 Z" fill="#f4f7fb"/>
  <path d="M10 25 L3 7 L15 7 L27 24 Z" fill="#3b7bf0"/>
  <path d="M54 30 L38 49 L51 49 L78 31 Z" fill="#c7d0de"/>
  <path d="M56 24 L47 6 L58 6 L76 24 Z" fill="#dfe5ee"/>
  <circle cx="74" cy="26" r="2.2" fill="#3b7bf0"/><circle cx="82" cy="26" r="2.2" fill="#3b7bf0"/><circle cx="90" cy="26" r="2.2" fill="#3b7bf0"/>
  <path d="M106 23 Q116 23 119 27 L106 27 Z" fill="#1d2a44"/>
</svg>`;

  let styled = false;
  function addStyle() {
    if (styled) return;
    styled = true;
    const s = document.createElement('style');
    s.textContent = CSS;
    document.head.appendChild(s);
  }
  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  const fmt = v => Math.round(v || 0).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
  const rnd = (a, b) => a + Math.random() * (b - a);
  const shuffle = a => { a = a.slice(); for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };
  function soundPref() { try { return localStorage.getItem('crm-win-sound') !== '0'; } catch (e) { return true; } }

  /* ---------- dźwięk (Web Audio, syntezowany na miejscu) ---------- */
  function makeSound(on) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    let ac;
    try { ac = new AC(); } catch (e) { return null; }
    const master = ac.createGain();
    master.gain.value = on ? .7 : 0;
    const comp = ac.createDynamicsCompressor();
    comp.connect(master);
    master.connect(ac.destination);
    const now = () => ac.currentTime;
    const sr = ac.sampleRate;
    function env(g, t, peak, a, hold, rel) {
      g.gain.setValueAtTime(.0001, t);
      g.gain.exponentialRampToValueAtTime(peak, t + a);
      g.gain.setValueAtTime(peak, t + a + hold);
      g.gain.exponentialRampToValueAtTime(.0001, t + a + hold + rel);
    }
    function tone(t, f, d, type, peak, cutoff) {
      const o = ac.createOscillator(), g = ac.createGain(), lp = ac.createBiquadFilter();
      o.type = type; o.frequency.setValueAtTime(f, t);
      lp.type = 'lowpass'; lp.frequency.value = cutoff || 2600;
      env(g, t, peak, .015, Math.max(.01, d - .1), .09);
      o.connect(lp); lp.connect(g); g.connect(comp);
      o.start(t); o.stop(t + d + .1);
    }
    let noise = null;
    function noiseBuf() {
      if (noise) return noise;
      noise = ac.createBuffer(1, sr * 2, sr);
      const d = noise.getChannelData(0);
      for (let i = 0; i < d.length; i++) d[i] = Math.random() * 2 - 1;
      return noise;
    }
    function burst(t, dur, freq, q, peak, type) {
      const s = ac.createBufferSource(), f = ac.createBiquadFilter(), g = ac.createGain();
      s.buffer = noiseBuf(); f.type = type || 'bandpass'; f.frequency.value = freq; f.Q.value = q;
      env(g, t, peak, .004, .01, dur);
      s.connect(f); f.connect(g); g.connect(comp);
      s.start(t, Math.random()); s.stop(t + dur + .05);
    }
    function play(buf, t, gain) {
      const s = ac.createBufferSource(), g = ac.createGain();
      s.buffer = buf; g.gain.value = gain;
      s.connect(g); g.connect(comp); s.start(t);
    }
    function boom(t, big) {
      const o = ac.createOscillator(), g = ac.createGain();
      o.type = 'sine'; o.frequency.setValueAtTime(big ? 130 : 110, t); o.frequency.exponentialRampToValueAtTime(34, t + .6);
      env(g, t, big ? 1 : .8, .008, .05, big ? .9 : .6);
      o.connect(g); g.connect(comp); o.start(t); o.stop(t + 1.1);
      burst(t, .3, 160, .7, .6, 'lowpass');
    }
    // szkolny dzwonek elektryczny: młoteczek uderza ~25 razy na sekundę w metalowy klosz → „brrrrr”
    function bellBuf(sec) {
      const n = Math.floor(sr * sec), b = ac.createBuffer(2, n, sr);
      const base = 1180, parts = [[1, 1], [2.02, .55], [2.76, .5], [4.1, .3], [5.4, .22], [6.8, .12]];
      for (let ch = 0; ch < 2; ch++) {
        const d = b.getChannelData(ch), rate = 25 + ch * .7, decay = Math.exp(-1 / (sr * .035)), det = 1 + ch * .002;
        let e = 0, next = 0;
        for (let i = 0; i < n; i++) {
          if (i >= next) { e = Math.min(1.4, e + 1); next += Math.floor(sr / rate); }
          e *= decay;
          const t = i / sr;
          let v = 0;
          for (const [m, a] of parts) v += a * Math.sin(2 * Math.PI * base * m * det * t);
          const fade = Math.min(1, t / .02, (sec - t) / .15);
          d[i] = (v * (.22 + e) * .32 + (Math.random() * 2 - 1) * e * .05) * fade;
        }
      }
      return b;
    }
    function applauseBuf(sec) {
      const b = ac.createBuffer(2, Math.floor(sr * sec), sr);
      for (let ch = 0; ch < 2; ch++) {
        const d = b.getChannelData(ch), n = Math.floor(330 * sec);
        for (let k = 0; k < n; k++) {
          const at = Math.floor(Math.random() * d.length), e = Math.sin(Math.PI * at / d.length);
          const len = Math.floor(sr * rnd(.004, .016)), amp = rnd(.15, .55) * e;
          for (let i = 0; i < len && at + i < d.length; i++) d[at + i] += (Math.random() * 2 - 1) * amp * Math.exp(-i / (len / 4));
        }
      }
      return b;
    }
    return {
      setOn(v) { master.gain.setTargetAtTime(v ? .7 : 0, now(), .04); },
      resume() { try { if (ac.state === 'suspended') ac.resume(); } catch (e) {} },
      close() { try { master.gain.setTargetAtTime(0, now(), .08); setTimeout(() => ac.close(), 400); } catch (e) {} },
      bell(at, sec) { play(bellBuf(sec), now() + at, 1); },
      boom(at, big) { boom(now() + at, big); },
      crash(at) { const t = now() + at; burst(t, 1.6, 7000, .4, .5, 'highpass'); burst(t, .9, 3200, .6, .35, 'bandpass'); },
      // dżingiel wygranej jak w automacie: szybkie arpeggio w górę
      jingle(at, root) {
        const t = now() + at, r = root || 523.25, steps = [1, 1.25, 1.5, 2, 2.5, 3, 4];
        steps.forEach((m, i) => { tone(t + i * .07, r * m, .16, 'square', .05, 5000); tone(t + i * .07, r * m, .16, 'triangle', .07, 6000); });
      },
      stinger(at, root) {  // poziom MEGA / JACKPOT: akord dęty + huk + talerz
        const t = now() + at;
        [1, 1.26, 1.5, 2].forEach(m => { tone(t, root * m, .7, 'sawtooth', .07, 2600); tone(t, root * m / 2, .7, 'square', .03, 1200); });
        boom(t, true); burst(t, 1.2, 7000, .4, .4, 'highpass');
      },
      fanfare(at) {
        const t = now() + at;
        const notes = [[0, 392, .14], [.15, 392, .14], [.3, 392, .14], [.45, 523.25, .5], [1.0, 659.25, .17], [1.19, 587.33, .17], [1.38, 783.99, 1.3]];
        for (const [dt, f, d] of notes) {
          tone(t + dt, f, d, 'sawtooth', .085, 2400);
          tone(t + dt, f * .5, d, 'square', .03, 1200);
          tone(t + dt, f * 1.26, d, 'triangle', .035, 3000);
        }
        boom(t); boom(t + 1.38, true);
      },
      tick(at, k) { tone(now() + at, 1400 + 900 * k, .035, 'square', .03, 6000); },
      clinks(at, sec, perSec) {  // monety spadające na stos
        const t0 = now() + at;
        for (let i = 0; i < sec * perSec; i++) {
          const t = t0 + Math.random() * sec, f = rnd(2600, 5200);
          tone(t, f, .06, 'sine', rnd(.02, .05), 9000); tone(t + .01, f * 1.48, .05, 'sine', .015, 9000);
        }
      },
      kaching(at) {
        const t = now() + at;
        tone(t, 1567.98, .12, 'triangle', .2, 6000);
        tone(t + .07, 2093, .6, 'triangle', .18, 7000);
        tone(t + .07, 3136, .5, 'sine', .06, 9000);
        burst(t, .06, 5000, 1, .25, 'highpass');
      },
      pop(at) {
        const t = now() + at;
        burst(t, .35, 700, .8, .7, 'bandpass');
        for (let i = 0; i < 9; i++) burst(t + .12 + Math.random() * .6, .04, 4500, 1, .12, 'highpass');
      },
      whoosh(at, dur) {
        const t = now() + at, s = ac.createBufferSource(), f = ac.createBiquadFilter(), g = ac.createGain();
        s.buffer = noiseBuf(); s.loop = true;
        f.type = 'bandpass'; f.Q.value = 1.2;
        f.frequency.setValueAtTime(300, t); f.frequency.exponentialRampToValueAtTime(1400, t + dur / 2); f.frequency.exponentialRampToValueAtTime(260, t + dur);
        g.gain.setValueAtTime(.0001, t); g.gain.exponentialRampToValueAtTime(.12, t + dur / 2); g.gain.exponentialRampToValueAtTime(.0001, t + dur);
        s.connect(f); f.connect(g); g.connect(comp); s.start(t); s.stop(t + dur + .05);
      },
      beeps(at) { const t = now() + at; [988, 1318, 880, 1568].forEach((f, i) => tone(t + i * .11, f, .09, 'square', .045, 5000)); },
      applause(at, sec) {
        const s = ac.createBufferSource(), f = ac.createBiquadFilter(), g = ac.createGain();
        s.buffer = applauseBuf(sec); f.type = 'bandpass'; f.frequency.value = 2200; f.Q.value = .6; g.gain.value = .9;
        s.connect(f); f.connect(g); g.connect(comp); s.start(now() + at);
      },
    };
  }

  /* ---------- teksty ---------- */
  function hypeLines(name, self) {
    const n = name || 'Mistrzu';
    const mine = [
      'To nie jest mały krok. To wielki krok dla 33Bots 🚀',
      `${n}, jesteś maszyną 🤖`,
      'Klient powiedział TAK. Cały świat to usłyszał 🌍',
      'Robot już pakuje walizkę ✈️',
      'Tego leada nikt by nie zamknął. Poza Tobą 😎',
      'Szampan schłodzony, robot naoliwiony 🍾',
      'Zapisane w historii firmy. Na zawsze 📜',
      'Chiny biją brawo. Cały zespół bije brawo 👏',
      `${n} = LEGENDA. Bez dyskusji 👑`,
    ];
    const team = [
      `Brawo, ${n}! Cały zespół bije brawo 👏`,
      'To nie jest mały krok. To wielki krok dla 33Bots 🚀',
      'Robot już pakuje walizkę ✈️',
      'Chiny biją brawo. Ty też bij! 👏',
      'Kto następny? Lead czeka na Ciebie 🔥',
    ];
    return shuffle(self ? mine : team);
  }

  /* ---------- canvas: monety, banknoty, konfetti, fajerwerki ---------- */
  function particles(cv) {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const W = innerWidth, H = innerHeight, small = W < 700;
    cv.width = W * dpr; cv.height = H * dpr;
    const ctx = cv.getContext('2d');
    ctx.scale(dpr, dpr);
    const parts = [];
    const MAX = small ? 260 : 520;
    const colors = ['#ffd166', '#fff3c4', '#7dffb0', '#3b7bf0', '#7fa8f5', '#ff8fb1', '#ffffff', '#ff5a5a'];
    const coinR = () => (small ? rnd(7, 12) : rnd(10, 18));
    function coin(x, y, vx, vy) {
      if (parts.length > MAX) return;
      parts.push({k: 'coin', x, y, vx, vy, r: coinR(), spin: Math.random() * 6, vs: rnd(.12, .3), life: 0, max: 400});
    }
    function note(x, y, vx, vy) {
      if (parts.length > MAX) return;
      parts.push({k: 'note', x, y, vx, vy, w: small ? 26 : 38, h: small ? 13 : 19, rot: Math.random() * 6, vr: rnd(-.15, .15), flip: Math.random() * 6, life: 0, max: 420});
    }
    let fountain = 0;
    function drawCoin(p) {
      const sx = Math.abs(Math.cos(p.spin)) * .9 + .1, r = p.r;
      ctx.save(); ctx.translate(p.x, p.y); ctx.scale(sx, 1);
      const g = ctx.createLinearGradient(-r, -r, r, r);
      g.addColorStop(0, '#fff6c2'); g.addColorStop(.45, '#ffcb3d'); g.addColorStop(1, '#b77c06');
      ctx.fillStyle = g; ctx.beginPath(); ctx.arc(0, 0, r, 0, Math.PI * 2); ctx.fill();
      ctx.lineWidth = Math.max(1.5, r * .16); ctx.strokeStyle = '#8a5a00'; ctx.stroke();
      ctx.lineWidth = 1; ctx.strokeStyle = 'rgba(255, 250, 220, .8)'; ctx.beginPath(); ctx.arc(0, 0, r * .62, 0, Math.PI * 2); ctx.stroke();
      ctx.restore();
    }
    function drawNote(p) {
      ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.rot); ctx.scale(1, Math.abs(Math.cos(p.flip)) * .8 + .2);
      ctx.fillStyle = '#3fae5a'; ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
      ctx.strokeStyle = '#1f6e35'; ctx.lineWidth = 2; ctx.strokeRect(-p.w / 2 + 2, -p.h / 2 + 2, p.w - 4, p.h - 4);
      ctx.fillStyle = '#d9f7df'; ctx.beginPath(); ctx.arc(0, 0, p.h * .28, 0, Math.PI * 2); ctx.fill();
      ctx.restore();
    }
    let alive = true;
    (function frame() {
      if (!alive) return;
      ctx.clearRect(0, 0, W, H);
      for (let i = 0; i < fountain; i++) coin(W / 2 + rnd(-30, 30), H + 20, rnd(-7, 7) * (small ? .6 : 1), -rnd(.022, .03) * H);
      for (let i = parts.length - 1; i >= 0; i--) {
        const p = parts[i];
        p.life++;
        if (p.k === 'coin') { p.vy += H * .00055; p.x += p.vx; p.y += p.vy; p.spin += p.vs; drawCoin(p); }
        else if (p.k === 'note') { p.vy = Math.min(p.vy + .12, 3.2); p.vx *= .985; p.x += p.vx + Math.sin(p.life / 12) * .8; p.y += p.vy; p.rot += p.vr; p.flip += .12; drawNote(p); }
        else if (p.k === 'c') {
          p.vy += .3; p.vx *= .99; p.x += p.vx; p.y += p.vy; p.r += p.vr;
          ctx.save(); ctx.translate(p.x, p.y); ctx.rotate(p.r); ctx.globalAlpha = Math.max(0, 1 - p.life / p.max);
          ctx.fillStyle = p.c; ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h * Math.abs(Math.cos(p.r))); ctx.restore();
        } else {
          p.px = p.x; p.py = p.y; p.vx *= .965; p.vy = p.vy * .965 + .07; p.x += p.vx; p.y += p.vy;
          ctx.globalAlpha = Math.max(0, 1 - p.life / p.max); ctx.strokeStyle = p.c; ctx.lineWidth = 2.4;
          ctx.beginPath(); ctx.moveTo(p.px, p.py); ctx.lineTo(p.x, p.y); ctx.stroke(); ctx.globalAlpha = 1;
        }
        if (p.life > p.max || p.y > H + 120) parts.splice(i, 1);
      }
      requestAnimationFrame(frame);
    })();
    return {
      W, H,
      setFountain(n) { fountain = small ? Math.ceil(n / 2) : n; },
      explode(n, notes) {  // wybuch pieniędzy ze środka ekranu
        const cx = W / 2, cy = H * .45, k = small ? .55 : 1;
        for (let i = 0; i < n * k; i++) { const a = rnd(0, Math.PI * 2), v = rnd(6, 20) * k; coin(cx, cy, Math.cos(a) * v, Math.sin(a) * v - 6); }
        for (let i = 0; i < (notes || 0) * k; i++) { const a = rnd(0, Math.PI * 2), v = rnd(4, 14) * k; note(cx, cy, Math.cos(a) * v, Math.sin(a) * v - 4); }
      },
      cannons() {  // armaty z dolnych rogów: monety, banknoty i konfetti
        for (let i = 0; i < (small ? 70 : 140); i++) {
          const left = i % 2 === 0, a = (left ? -62 : -118) * Math.PI / 180 + rnd(-.45, .45), v = rnd(12, 24) * (small ? .7 : 1);
          const x = left ? 0 : W, y = H * .85;
          if (i % 3 === 0) coin(x, y, Math.cos(a) * v, Math.sin(a) * v);
          else if (i % 3 === 1) note(x, y, Math.cos(a) * v * .8, Math.sin(a) * v * .8);
          else parts.push({k: 'c', x, y, vx: Math.cos(a) * v * .7, vy: Math.sin(a) * v * .7, w: rnd(6, 12), h: rnd(8, 16), r: Math.random() * 6,
                           vr: rnd(-.3, .3), c: colors[i % colors.length], life: 0, max: 260});
        }
      },
      rain(n) { for (let i = 0; i < n * (small ? .5 : 1); i++) note(Math.random() * W, -30 - Math.random() * 300, rnd(-1, 1), rnd(1, 2.5)); },
      firework(x, y, pal) {
        x = x == null ? rnd(.15, .85) * W : x; y = y == null ? rnd(.1, .4) * H : y;
        const n = 70, base = pal || [colors[Math.floor(Math.random() * 3)], '#fff3c4', '#ff5a5a'];
        for (let i = 0; i < n; i++) {
          const a = (i / n) * Math.PI * 2, v = rnd(2.5, 6.5);
          parts.push({k: 'f', x, y, vx: Math.cos(a) * v, vy: Math.sin(a) * v, c: base[i % base.length], life: 0, max: rnd(55, 85), px: x, py: y});
        }
      },
      stop() { alive = false; },
    };
  }

  /* ---------- pokaz ---------- */
  window.crmCelebrate = function (lead, opts) {
    lead = lead || {};
    opts = opts || {};
    addStyle();
    const me = window.CRM_ME || {};
    const calm = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
    let self = opts.self !== false;
    let who = self ? (me.name || '') : '';
    const value = +lead.value || 0, cur = lead.currency || '';
    const timers = [];
    const later = (ms, fn) => timers.push(setTimeout(fn, ms));
    const page = [...document.body.children].filter(el => !/^(SCRIPT|STYLE|LINK)$/.test(el.tagName));

    return new Promise(resolve => {
      const root = document.createElement('div');
      root.className = 'wg' + (calm ? ' wg-calm wg-go' : '');
      root.setAttribute('role', 'dialog');
      root.setAttribute('aria-label', 'Wygrana');
      root.innerHTML = `<div class="wg-bg"></div><div class="wg-stage"><div class="wg-rays"></div>
        <div class="wg-beacon wg-b1"></div><div class="wg-beacon wg-b2"></div>
        <div class="wg-alarm"><div class="wg-bell">🔔</div><div class="wg-alarm-txt">🚨 UWAGA! ZAMYKANIE TEMATU 🚨</div></div>
        <div class="wg-bulbs"><i></i><i></i><i></i><i></i></div>
        ${calm ? '' : '<canvas class="wg-cv"></canvas>'}
        <div class="wg-center">
          <div class="wg-kicker"></div>
          <div class="wg-title"></div>
          <div class="wg-company">🏆 ${esc(lead.company || '')}</div>
          ${value ? `<div class="wg-money">+<span class="wg-num">0</span> ${esc(cur)}</div>` : ''}
          <div class="wg-hype"></div>
          <div class="wg-chips"></div>
          <button type="button" class="wg-next">Dawaj następnego! 🚀</button>
        </div>
        <div class="wg-robot">${ROBOT}<div class="wg-bubble"></div></div></div>
        <button type="button" class="wg-mute" title="Dźwięk pokazu"></button>`;
      document.body.appendChild(root);
      const $ = s => root.querySelector(s);
      const stage = $('.wg-stage');
      const kicker = $('.wg-kicker'), title = $('.wg-title'), hype = $('.wg-hype'), chips = $('.wg-chips'), bubble = $('.wg-bubble'), mute = $('.wg-mute');

      let soundOn = soundPref();
      const snd = calm ? null : makeSound(soundOn);
      const paintMute = () => { mute.textContent = soundOn ? '🔊' : '🔇'; };
      paintMute();
      mute.addEventListener('click', e => {
        e.stopPropagation();
        soundOn = !soundOn;
        try { localStorage.setItem('crm-win-sound', soundOn ? '1' : '0'); } catch (err) {}
        if (snd) { snd.resume(); snd.setOn(soundOn); }
        paintMute();
      });
      root.addEventListener('pointerdown', () => { if (snd) snd.resume(); });

      // dane z CRM: kto zamknął, która wygrana w miesiącu, suma, rekord
      let stats = null;
      const setTexts = () => {
        const name = who || (stats && stats.by_mail ? 'Klient' : 'Zespół');
        kicker.textContent = stats && stats.by_mail ? '📩 KLIENT SAM POTWIERDZIŁ MAILEM' : `🔥 ${name.toUpperCase()} ZAMYKA TEMAT 🔥`;
        bubble.innerHTML = self ? `Brawo, ${esc(name)}! 谢谢! 👏` : `${esc(name)}, szacun! 谢谢! 👏`;
      };
      if (lead.id) {
        fetch(`/crm/api/leads/${lead.id}/wygrana`, {headers: {'X-Requested-With': 'fetch'}})
          .then(r => (r.ok ? r.json() : null)).then(d => {
            if (!d || !d.id) return;
            stats = d;
            if (d.who_id && me.id) self = d.who_id === me.id;
            if (d.who) who = d.who;
            setTexts();
          }).catch(() => {});
      }
      setTexts();

      function showChips() {
        if (!stats || !(stats.n > 0)) return;
        const list = [];
        if (stats.record) list.push(['👑 REKORD MIESIĄCA', true]);
        list.push([`🏆 ${stats.n}. wygrana w ${stats.month}`, false]);
        list.push([`💰 Razem w ${stats.month}: ${stats.sum} ${stats.currency}`, false]);
        if (stats.who && stats.mine > 1) list.push([`⭐ ${stats.who}: ${stats.mine} w tym miesiącu`, false]);
        chips.innerHTML = list.map(([t, gold], i) => `<span class="wg-chip${gold ? ' wg-gold' : ''}" style="animation-delay:${i * .18}s${gold ? ', .6s' : ''}">${esc(t)}</span>`).join('');
      }
      function setTitle(text, cls) {
        title.className = 'wg-title' + (cls ? ' ' + cls : '');
        title.style.fontSize = '';
        title.innerHTML = [...text].map((ch, i) => ch === ' ' ? '<span>&nbsp;</span>'
          : `<span style="animation-delay:${(i * (cls ? .025 : .045)).toFixed(3)}s, 0s${cls === 'wg-jackpot' ? ', .7s' : ''}">${esc(ch)}</span>`).join('');
        const max = title.clientWidth, w = title.scrollWidth;  // długie imię / napis: zmniejsz, żeby zmieścić w ekranie
        if (w > max) title.style.fontSize = (parseFloat(getComputedStyle(title).fontSize) * max / w) + 'px';
      }
      function closer() {
        if (stats && stats.by_mail) return 'KLIENT SAM SIĘ PODDAŁ!';
        return `${(who || 'ZESPÓŁ').toUpperCase()} ZAMYKA TEMAT!`;
      }
      function shake(strong) {
        for (const el of [stage, ...(strong ? page : [])]) {
          const c = el === stage ? 'wg-shake' : 'wg-page-shake';
          el.classList.remove(c); void el.offsetWidth; el.classList.add(c);
          setTimeout(() => el.classList.remove(c), 600);
        }
      }

      let done = false, fx = null;
      function close() {
        if (done) return;
        done = true;
        timers.forEach(clearTimeout);
        document.removeEventListener('keydown', onKey);
        page.forEach(el => el.classList.remove('wg-page-quake', 'wg-page-shake'));
        root.classList.add('wg-out');
        if (snd) snd.close();
        setTimeout(() => { if (fx) fx.stop(); root.remove(); resolve(); }, 460);
      }
      const onKey = e => { if (e.key === 'Escape' || e.key === 'Enter') { e.preventDefault(); close(); } };
      document.addEventListener('keydown', onKey);
      $('.wg-next').addEventListener('click', close);

      // licznik kwoty: tyka coraz wyżej, na końcu wielkie „ka-ching”
      const num = $('.wg-num');
      function countUp(dur) {
        if (!num) return;
        const t0 = performance.now();
        let ticks = 0;
        (function tick(t) {
          const k = Math.min(1, (t - t0) / dur);
          num.textContent = fmt(value * (1 - Math.pow(1 - k, 2.2)));
          const step = Math.floor(k * dur / 70);
          if (step > ticks && k < 1) { ticks = step; if (snd) snd.tick(0, k); }
          if (k < 1 && !done) requestAnimationFrame(tick);
          else if (k >= 1) {
            if (snd) snd.kaching(0);
            const m = $('.wg-money'); if (m) { m.classList.remove('wg-bump'); void m.offsetWidth; m.classList.add('wg-bump'); }
          }
        })(t0);
      }

      // hasła co 1,7 s
      const lines = hypeLines(who, self);
      let li = 0;
      function nextLine() {
        if (done) return;
        hype.innerHTML = `<span>${esc(lines[li % lines.length])}</span>`;
        li++;
        later(1700, nextLine);
      }

      if (calm) {
        setTitle('WYGRANA!');
        later(200, () => countUp(1200)); later(400, nextLine); later(900, showChips); later(9000, close);
        return;
      }

      fx = particles($('.wg-cv'));
      const {W, H} = fx;
      const T1 = ALARM_MS, T2 = T1 + TIER_MS, T3 = T2 + TIER_MS, TEND = T1 + COUNT_MS, FIN = T3 + 900;

      // 1. ALARM: dzwonek „brrrr” i trzęsienie ziemi całej strony
      if (snd) snd.bell(0, ALARM_MS / 1000 + .05);
      stage.classList.add('wg-quake');
      page.forEach(el => el.classList.add('wg-page-quake'));

      // 2. WYGRANA! — huk, wybuch monet, fontanna, licznik
      later(T1, () => {
        stage.classList.remove('wg-quake');
        page.forEach(el => el.classList.remove('wg-page-quake'));
        root.classList.add('wg-go');
        const fl = document.createElement('div'); fl.className = 'wg-flash'; stage.appendChild(fl); setTimeout(() => fl.remove(), 900);
        setTitle('WYGRANA!');
        shake(true);
        fx.explode(90, 20);
        fx.setFountain(3);
        countUp(COUNT_MS);
        if (snd) { snd.boom(0, true); snd.jingle(.05, 523.25); snd.clinks(.2, COUNT_MS / 1000, 9); }
      });
      // 3. JERZY ZAMYKA TEMAT! (imię osoby, która zamknęła)
      later(T2, () => {
        setTitle(closer(), 'wg-mega wg-slam');
        shake(true);
        fx.explode(110, 30);
        fx.setFountain(5);
        fx.firework(W * .2, H * .25); fx.firework(W * .8, H * .25);
        if (snd) { snd.stinger(0, 392); snd.jingle(.25, 659.25); }
      });
      // 4. TEMAT ZAMKNIĘTY! — armata pieniędzy, fajerwerki, fanfara
      later(T3, () => {
        root.classList.add('wg-jack');
        setTitle('TEMAT ZAMKNIĘTY!', 'wg-jackpot');
        shake(true);
        const fl = document.createElement('div'); fl.className = 'wg-flash'; stage.appendChild(fl); setTimeout(() => fl.remove(), 900);
        fx.explode(170, 60);
        fx.cannons();
        fx.setFountain(7);
        if (snd) { snd.stinger(0, 523.25); snd.crash(.05); snd.fanfare(.35); }
      });
      later(TEND + 200, () => fx.setFountain(2));
      later(TEND + 2200, () => fx.setFountain(0));
      later(T3 + 1800, () => fx.cannons());
      [0, 500, 1000, 1500, 2200, 3000, 3900, 4900, 6000].forEach((ms, i) => later(T3 + ms, () => {
        fx.firework(null, null, i % 2 ? ['#ff5a5a', '#ffd166', '#fff3c4'] : null);
        if (snd) snd.pop(0);
      }));
      later(T3 + 2600, () => fx.rain(60));

      // 5. finał: robot, hasła, liczby, przycisk, samoloty, smok, lampiony, oklaski
      later(FIN, () => { $('.wg-robot').classList.add('wg-show'); if (snd) { snd.beeps(.6); snd.applause(.2, 6.5); } });
      later(FIN + 300, nextLine);
      later(FIN + 600, showChips);
      later(FIN + 1000, () => $('.wg-next').classList.add('wg-show'));
      const banners = [
        {text: `🏆 ${lead.company || 'WYGRANA'}`, dir: 'r', top: '5%', dur: 6.8, at: FIN},
        {text: '', dir: 'l', top: '13%', dur: 7.4, at: FIN + 1300, who: true},
        {text: value ? `+${fmt(value)} ${cur} 💸` : 'KOLEJNY KLIENT 🎉', dir: 'r', top: '80%', dur: 6.2, at: FIN + 2800},
      ];
      banners.forEach(b => later(b.at, () => {
        const text = b.who ? (self ? `${(who || 'TY').toUpperCase()} = LEGENDA 👑` : `BRAWO ${(who || 'ZESPÓŁ').toUpperCase()}! 👏`) : b.text;
        const el = document.createElement('div');
        el.className = `wg-plane wg-${b.dir}`;
        el.style.top = b.top;
        el.style.setProperty('--dur', b.dur + 's');
        el.innerHTML = `<div class="wg-plane-in"><div class="wg-banner">${esc(text)}</div><div class="wg-rope"></div>${PLANE}</div>`;
        stage.appendChild(el);
        if (snd) snd.whoosh(0, 2.2);
      }));
      later(FIN + 200, () => ['7%', '19%', '81%', '93%'].forEach((x, i) => {
        const l = document.createElement('div');
        l.className = 'wg-lantern'; l.textContent = '🏮'; l.style.left = x; l.style.top = '80px';
        l.style.animationDelay = `${i * .15}s, ${1 + i * .15}s`;
        stage.appendChild(l);
      }));
      later(FIN + 1800, () => { const d = document.createElement('div'); d.className = 'wg-dragon'; d.textContent = '🐉'; stage.appendChild(d); });
      for (let i = 0; i < 28; i++) later(FIN + 300 + i * 190, () => {
        const c = document.createElement('div');
        c.className = 'wg-clap'; c.textContent = i % 5 === 4 ? '🙌' : '👏';
        c.style.left = rnd(4, 92) + '%';
        c.style.setProperty('--t', rnd(2, 3.2) + 's');
        c.style.setProperty('--r', rnd(-25, 25) + 'deg');
        stage.appendChild(c);
        setTimeout(() => c.remove(), 3400);
      });

      later(FIN + 11000, close);
    });
  };

  // wygrana zapisana przed przeładowaniem strony (np. zapis formularza edycji leada) — pokaż po wczytaniu
  document.addEventListener('DOMContentLoaded', () => {
    let w = null;
    try { w = JSON.parse(sessionStorage.getItem('crm-win') || 'null'); sessionStorage.removeItem('crm-win'); } catch (e) {}
    if (w) window.crmCelebrate(w, {self: true});
  });
})();
