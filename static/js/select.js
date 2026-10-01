document.querySelectorAll("input.code").forEach((el) => {
  el.addEventListener("focus", () => el.select());
});
