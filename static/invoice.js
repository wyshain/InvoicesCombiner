const files = document.getElementById("files");
const selection = document.getElementById("selection");

files.addEventListener("change", () => {
    const count = files.files.length;
    selection.hidden = count === 0;
    selection.textContent = count ? `已选 ${count} 份，预计生成 ${Math.ceil(count / 3)} 页 A4。` : "";
});

const feedbackForm = document.getElementById("feedback-form");
const feedbackText = document.getElementById("feedback-text");
const feedbackStatus = document.getElementById("feedback-status");
const feedbackButton = feedbackForm.querySelector("button[type=submit]");

feedbackForm.addEventListener("submit", async event => {
    event.preventDefault();
    const message = feedbackText.value.trim();
    if (!message) {
        feedbackStatus.textContent = "请输入反馈内容。";
        feedbackText.focus();
        return;
    }
    feedbackButton.disabled = true;
    feedbackStatus.textContent = "正在提交…";
    try {
        const response = await fetch(feedbackForm.action, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({message})
        });
        const result = await response.json();
        if (!response.ok || !result.ok) throw new Error(result.error || "提交失败，请重试。");
        feedbackText.value = "";
        feedbackStatus.textContent = "反馈已提交，谢谢。";
    } catch (error) {
        feedbackStatus.textContent = error instanceof TypeError ? "无法连接，请稍后重试。" : error.message;
    } finally {
        feedbackButton.disabled = false;
    }
});
