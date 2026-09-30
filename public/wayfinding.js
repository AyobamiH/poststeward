/* Navigation only: no requests, persistence or operational authority. */
function navigateToSection() {
  let id;
  try { id = decodeURIComponent(location.hash.slice(1)); } catch { return; }
  const target = id && document.getElementById(id);
  if (!target || target.closest('[hidden]')) return;
  for (let parent = target.parentElement; parent; parent = parent.parentElement) {
    if (parent.tagName === 'DETAILS') parent.open = true;
  }
  document.querySelectorAll('.product-nav a, .product-menu-panel a').forEach(link => {
    const url = new URL(link.href);
    const active = url.pathname === location.pathname && url.hash === location.hash;
    if (active) link.setAttribute('aria-current', 'location');
    else link.removeAttribute('aria-current');
  });
  target.scrollIntoView({block:'start'});
  const heading = target.matches('main,h1,h2,h3') ? target : target.querySelector('h1,h2,h3');
  if (heading) { heading.tabIndex = -1; heading.focus({preventScroll:true}); }
}
addEventListener('hashchange', () => requestAnimationFrame(navigateToSection));
document.addEventListener('click', event => {
  const link = event.target.closest?.('a[href]');
  if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const menu = link.closest('.site-menu, .product-menu, .docs-mobile-menu');
  if (menu) menu.open = false;
  const url = new URL(link.href);
  if (url.origin === location.origin && url.pathname === location.pathname && url.hash === location.hash && url.hash) navigateToSection();
});
if (location.hash) requestAnimationFrame(navigateToSection);
document.addEventListener('workspace-ready', () => { if (location.hash) navigateToSection(); });
document.addEventListener('keydown', event => {
  if (event.key !== 'Escape') return;
  const menu = event.target.closest('.site-menu, .product-menu, .docs-mobile-menu');
  if (menu?.open) { menu.open = false; menu.querySelector('summary')?.focus(); }
});
