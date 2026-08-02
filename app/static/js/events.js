/* CSP-safe event delegation for static and dynamically rendered controls. */
document.addEventListener('click', event => {
  const target = event.target.closest('[data-action]');
  if (!target) return;

  const action = target.dataset.action;
  if (action === 'toggle-theme') toggleTheme();
  else if (action === 'trigger-scan') triggerScan();
  else if (action === 'toggle-login') toggleLogin();
  else if (action === 'dismiss-telegram-banner') {
    const banner = document.getElementById('tg-unlinked-banner');
    if (banner) banner.style.display = 'none';
  }
  else if (action === 'switch-tab') switchTab(target.dataset.tab);
  else if (action === 'toggle-country-drop') toggleCountryDrop(event);
  else if (action === 'country-all') msAll(event);
  else if (action === 'country-none') msNone(event);
  else if (action === 'upload-resume') document.getElementById('resume-file')?.click();
  else if (action === 'toggle-work-mode-drop') toggleWMDrop(event);
  else if (action === 'set-work-mode') {
    setWorkMode(target.dataset.mode);
    closeWMDrop();
  }
  else if (action === 'set-ui-language') setLang(target.dataset.lang);
  else if (action === 'save-profile') saveProfile();
  else if (action === 'load-profile') loadProfile();
  else if (action === 'set-ops-window') setOpsWindow(Number(target.dataset.opsWindow));
  else if (action === 'refresh-ops') loadOpsOverview(true);
  else if (action === 'close-modal') closeModal();
  else if (action === 'set-language-level') {
    event.stopPropagation();
    _setLangLevel(target.dataset.languageCode, target.dataset.languageLevel);
  }
  else if (action === 'remove-language') {
    event.stopPropagation();
    _removeLang(target.dataset.languageCode);
  }
  else if (action === 'remove-excluded-keyword') {
    _removeExcludedKw(Number(target.dataset.keywordIndex));
  }
  else if (action === 'open-jobs') {
    const options = { tab: target.dataset.tab || 'jobs' };
    if (target.dataset.minScore !== undefined) options.minScore = Number(target.dataset.minScore);
    if (target.dataset.source !== undefined) options.source = target.dataset.source;
    openJobsView(options);
  }
  else if (action === 'ops-card') handleOpsCardAction(target.dataset.opsAction);
  else if (action === 'view-admin-user') {
    const userId = Number(target.dataset.userId);
    if (Number.isSafeInteger(userId) && userId > 0) viewAdminUserProfile(userId);
  }
  else if (action === 'delete-user') {
    const userId = Number(target.dataset.userId);
    if (Number.isSafeInteger(userId) && userId > 0) deleteUser(userId, target.dataset.userName || '');
  }
});

document.addEventListener('change', event => {
  if (event.target.matches('[data-change-action="country-toggle"]')) onCountryToggle();
});
