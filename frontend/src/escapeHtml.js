/**
 * HTML escaping utility — prevents XSS when inserting untrusted data into innerHTML.
 *
 * Usage:
 *   import { esc, escAttr } from './escapeHtml.js';
 *   container.innerHTML = `<span>${esc(userInput)}</span>`;
 *   el.innerHTML = `<button onclick="fn('${escAttr(name)}')">Go</button>`;
 */

const ESC_MAP = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
};

/**
 * Escape a string for safe insertion into HTML content.
 * @param {*} str — value to escape (coerced to string)
 * @returns {string}
 */
export function esc(str) {
    if (str == null) return '';
    return String(str).replace(/[&<>"']/g, ch => ESC_MAP[ch]);
}

/**
 * Escape a string for safe insertion into an HTML attribute or inline JS string.
 * Escapes the same chars as esc() plus backtick.
 * @param {*} str — value to escape
 * @returns {string}
 */
export function escAttr(str) {
    if (str == null) return '';
    return String(str).replace(/[&<>"'`]/g, ch => ESC_MAP[ch] || '&#96;');
}
