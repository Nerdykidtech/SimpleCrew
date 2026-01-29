/**
 * @file formatters.js
 * @description Currency formatting and card color constants
 */

// Currency formatter
const fmt = (num) => "$" + num.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});

// Card color mapping
const cardColors = {
    'DENIM': '#4a69bd',
    'TEAL': '#0abde3',
    'BEIGE': '#d1ccc0',
    'BLACK': '#2f3640'
};
