(() => {
  const toggle = document.querySelector(".password-toggle");
  if (!toggle) return;

  const passwordInput = document.getElementById(toggle.getAttribute("aria-controls"));
  if (!passwordInput) return;

  toggle.addEventListener("click", () => {
    const passwordIsVisible = passwordInput.type === "text";
    passwordInput.type = passwordIsVisible ? "password" : "text";
    const actionLabel = passwordIsVisible ? "Show password" : "Hide password";
    toggle.setAttribute("aria-label", actionLabel);
    toggle.title = actionLabel;
    toggle.setAttribute("aria-pressed", String(!passwordIsVisible));
  });
})();
