/* Readable HTML commands remain available when JavaScript or clipboard access fails. */
document.querySelectorAll('[data-copy]').forEach(button => {
  button.addEventListener('click', async () => {
    const target = document.getElementById(button.dataset.copy);
    let feedback = document.getElementById(button.dataset.copyFeedback || '');
    if (!feedback) { feedback=document.createElement('p'); feedback.className='copy-feedback'; feedback.setAttribute('role','status'); feedback.setAttribute('aria-live','polite'); (button.closest('.code-block') || button.parentElement).append(feedback); }
    if (!target) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(target.textContent.trim());
      if (feedback) feedback.textContent = 'Copied to clipboard.';
      else { button.textContent = 'Copied'; button.setAttribute('aria-label', 'Copied to clipboard'); }
    } catch {
      if (feedback) feedback.textContent = 'Clipboard unavailable. Select and copy the command above.';
      else { button.textContent = 'Select and copy the code'; button.setAttribute('aria-label', 'Clipboard unavailable. Select and copy the code.'); }
    }
  });
});
