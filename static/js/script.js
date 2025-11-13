// assets/script.js
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".thread").forEach(thread => {
    const replies = thread.querySelectorAll(".reply");
    if (replies.length > 1) {
      const btn = document.createElement("button");
      btn.textContent = `💬 ${replies.length}件の返信を表示`;
      btn.className = "toggle-thread";
      thread.insertBefore(btn, thread.firstChild);
      replies.forEach(r => (r.style.display = "none"));
      btn.addEventListener("click", () => {
        replies.forEach(r => (r.style.display = r.style.display === "none" ? "block" : "none"));
        btn.textContent = btn.textContent.includes("表示")
          ? "返信を隠す"
          : `💬 ${replies.length}件の返信を表示`;
      });
    }
  });
});
