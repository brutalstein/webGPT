INPUT_SELECTORS = (
    'rich-textarea div[contenteditable="true"]',
    'div.ql-editor[contenteditable="true"]',
    '[contenteditable="true"][role="textbox"]',
    'textarea[aria-label*="prompt" i]',
    'textarea[placeholder*="Gemini" i]',
)

RESPONSE_SELECTORS = (
    "model-response",
    'div[data-message-author-role="model"]',
    'div[data-message-author-role="assistant"]',
    "model-response .message-content",
)

SEND_BUTTON_SELECTORS = (
    'button[data-test-id="send-button"]',
    'button[aria-label*="Send" i]',
    'button[aria-label*="Gönder" i]',
)

STOP_BUTTON_SELECTORS = (
    'button[data-test-id="stop-button"]',
    'button[aria-label*="Stop response" i]',
    'button[aria-label*="Stop generating" i]',
    'button[aria-label*="Yanıtı durdur" i]',
    'button[aria-label*="Oluşturmayı durdur" i]',
)

NEW_CHAT_NAMES = ("New chat", "Yeni sohbet")
