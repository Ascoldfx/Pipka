/* CSP-safe event delegation for static and dynamically rendered controls. */
document.addEventListener('click', event => {
  const target = event.target.closest('[data-action]');
  if (!target) return;

  const action = target.dataset.action;
  if (action === 'toggle-theme') (window.toggleTheme || (typeof toggleTheme !== 'undefined' ? toggleTheme : null))?.();
  else if (action === 'trigger-scan') (window.triggerScan || (typeof triggerScan !== 'undefined' ? triggerScan : null))?.();
  else if (action === 'toggle-login') (window.toggleLogin || (typeof toggleLogin !== 'undefined' ? toggleLogin : null))?.();
  else if (action === 'dismiss-telegram-banner') {
    const banner = document.getElementById('tg-unlinked-banner');
    if (banner) banner.style.display = 'none';
  }
  else if (action === 'switch-tab') (window.switchTab || (typeof switchTab !== 'undefined' ? switchTab : null))?.(target.dataset.tab);
  else if (action === 'toggle-country-drop') (window.toggleCountryDrop || (typeof toggleCountryDrop !== 'undefined' ? toggleCountryDrop : null))?.(event);
  else if (action === 'country-all') (window.msAll || (typeof msAll !== 'undefined' ? msAll : null))?.(event);
  else if (action === 'country-none') (window.msNone || (typeof msNone !== 'undefined' ? msNone : null))?.(event);
  else if (action === 'upload-resume') document.getElementById('resume-file')?.click();
  else if (action === 'toggle-work-mode-drop') (window.toggleWMDrop || (typeof toggleWMDrop !== 'undefined' ? toggleWMDrop : null))?.(event);
  else if (action === 'set-work-mode') {
    (window.setWorkMode || (typeof setWorkMode !== 'undefined' ? setWorkMode : null))?.(target.dataset.mode);
    (window.closeWMDrop || (typeof closeWMDrop !== 'undefined' ? closeWMDrop : null))?.();
  }
  else if (action === 'set-ui-language') (window.setLang || (typeof setLang !== 'undefined' ? setLang : null))?.(target.dataset.lang);
  else if (action === 'save-profile') (window.saveProfile || (typeof saveProfile !== 'undefined' ? saveProfile : null))?.();
  else if (action === 'load-profile') (window.loadProfile || (typeof loadProfile !== 'undefined' ? loadProfile : null))?.();
  else if (action === 'set-ops-window') (window.setOpsWindow || (typeof setOpsWindow !== 'undefined' ? setOpsWindow : null))?.(Number(target.dataset.opsWindow));
  else if (action === 'refresh-ops') (window.loadOpsOverview || (typeof loadOpsOverview !== 'undefined' ? loadOpsOverview : null))?.(true);
  else if (action === 'close-modal') (window.closeModal || (typeof closeModal !== 'undefined' ? closeModal : null))?.();
  else if (action === 'set-language-level') {
    event.stopPropagation();
    (window._setLangLevel || (typeof _setLangLevel !== 'undefined' ? _setLangLevel : null))?.(target.dataset.languageCode, target.dataset.languageLevel);
  }
  else if (action === 'remove-language') {
    event.stopPropagation();
    (window._removeLang || (typeof _removeLang !== 'undefined' ? _removeLang : null))?.(target.dataset.languageCode);
  }
  else if (action === 'remove-excluded-keyword') {
    (window._removeExcludedKw || (typeof _removeExcludedKw !== 'undefined' ? _removeExcludedKw : null))?.(Number(target.dataset.keywordIndex));
  }
  else if (action === 'open-jobs') {
    const options = { tab: target.dataset.tab || 'jobs' };
    if (target.dataset.minScore !== undefined) options.minScore = Number(target.dataset.minScore);
    if (target.dataset.source !== undefined) options.source = target.dataset.source;
    if (window.openJobsView) window.openJobsView(options);
  }
  else if (action === 'ops-card') if (window.handleOpsCardAction) window.handleOpsCardAction(target.dataset.opsAction);
  else if (action === 'open-feedback') (window.openFeedbackModal || (typeof openFeedbackModal !== 'undefined' ? openFeedbackModal : null))?.();
  else if (action === 'close-feedback') (window.closeFeedbackModal || (typeof closeFeedbackModal !== 'undefined' ? closeFeedbackModal : null))?.();
  else if (action === 'submit-feedback') (window.submitFeedback || (typeof submitFeedback !== 'undefined' ? submitFeedback : null))?.();
  else if (action === 'complete-onboarding') (window.completeOnboarding || (typeof completeOnboarding !== 'undefined' ? completeOnboarding : null))?.();
  else if (action === 'start-checkout') (window.startCheckout || (typeof startCheckout !== 'undefined' ? startCheckout : null))?.(target.dataset.tier);
  else if (action === 'test-fulfill') (window.testFulfill || (typeof testFulfill !== 'undefined' ? testFulfill : null))?.(target.dataset.txId);
  else if (action === 'view-admin-user') {
    const userId = Number(target.dataset.userId);
    if (Number.isSafeInteger(userId) && userId > 0 && window.viewAdminUserProfile) window.viewAdminUserProfile(userId);
  }
  else if (action === 'delete-user') {
    const userId = Number(target.dataset.userId);
    if (Number.isSafeInteger(userId) && userId > 0 && window.deleteUser) window.deleteUser(userId, target.dataset.userName || '');
  }
});

document.addEventListener('change', event => {
  if (event.target.matches('[data-change-action="country-toggle"]')) onCountryToggle();
  else if (event.target.id === 'guest-lang-dropdown' || event.target.matches('[data-change-action="switch-guest-lang"]')) {
    if (typeof window.switchGuestLanguage === 'function') {
      window.switchGuestLanguage(event.target.value);
    }
  }
});
