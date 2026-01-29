/**
 * @file state.js
 * @description Global application state management
 */

// Global state variables
let goalsDataStore = [];
let allGroups = [];
let currentBalances = { checking: 0, savings: 0 };
let expensesDataStore = [];
let moveMoneyAccounts = [];
let currentFundingSource = "Checking";
let showCreditCardPockets = localStorage.getItem('showCreditCardPockets') === 'true';

// Credit card integration state
let selectedProvider = null;
let simpleFinAccessUrl = null;
let pendingAccountId = null;
let pendingAccountName = null;
let creditCardRefreshInterval = null;

// Transaction filter state
let filterState = { q: '', minDate: '', maxDate: '', minAmt: '', maxAmt: '' };

// Transaction auto-refresh interval
let transactionRefreshInterval = null;
