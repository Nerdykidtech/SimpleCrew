<p align="center">
  <img src="static/images/512.png" alt="SimpleCrew Logo" width="120" height="120">
</p>

<h1 align="center">SimpleCrew</h1>

<p align="center">
  <strong>A modern, feature-rich dashboard for Crew Banking</strong>
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#screenshots">Screenshots</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#api-reference">API</a> •
  <a href="#contributing">Contributing</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9+-blue?style=flat-square&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/Flask-2.0+-green?style=flat-square&logo=flask" alt="Flask">
  <img src="https://img.shields.io/badge/Docker-Ready-2496ED?style=flat-square&logo=docker" alt="Docker">
  <img src="https://img.shields.io/badge/PWA-Enabled-5A0FC8?style=flat-square" alt="PWA">
  <img src="https://img.shields.io/badge/License-MIT-yellow?style=flat-square" alt="License">
</p>

---

## Overview

SimpleCrew is a comprehensive financial dashboard that connects to [Crew Banking](https://trycrew.com) to provide enhanced money management features. Built with a focus on usability and modern design, it offers real-time transaction tracking, intelligent expense management, family account oversight, and a beautiful dark mode experience.

### Why SimpleCrew?

- **Enhanced Visibility** - See your Safe-to-Spend, bills, and pockets all in one place
- **Family Management** - Easily monitor and manage your children's accounts and cards
- **Smart Categorization** - Automatic bill detection and expense tracking
- **Modern UI/UX** - Clean, responsive design with dark mode support
- **PWA Ready** - Install on any device for an app-like experience

---

## Features

### 💰 Financial Dashboard
| Feature | Description |
|---------|-------------|
| **Safe-to-Spend** | Real-time calculation of available spending money |
| **Transaction History** | Searchable, filterable list with smart categorization |
| **Balance Tracking** | Historical data with trend analysis |
| **Money Transfers** | Seamlessly move funds between accounts and pockets |

### 📊 Expense Management
| Feature | Description |
|---------|-------------|
| **Bill Tracking** | Automatic reservations and funding schedules |
| **Progress Visualization** | Visual progress bars for each expense |
| **Smart Funding** | Estimated next funding amounts and dates |
| **Variable Bills** | Support for bills with fluctuating amounts |

### 🎯 Savings Pockets
| Feature | Description |
|---------|-------------|
| **Goal-Based Saving** | Create pockets with target amounts |
| **Group Organization** | Organize pockets into custom groups |
| **Quick Transfers** | One-click funding from Safe-to-Spend |
| **Activity History** | View all transactions per pocket |

### 💳 Card Management
| Feature | Description |
|---------|-------------|
| **Physical & Virtual Cards** | Full visibility of all card types |
| **Spend Source Control** | Assign cards to specific pockets |
| **Bill-Attached Cards** | Visual indicator for bill-linked cards |
| **Family Card Support** | View and manage children's cards |

### 👨‍👩‍👧‍👦 Family Accounts
| Feature | Description |
|---------|-------------|
| **Kids Dashboard** | Dedicated view for each child's account |
| **Balance Monitoring** | Real-time checking balances |
| **Card Overview** | See all cards assigned to each child |
| **Allowance Tracking** | View scheduled allowance information |

### 💳 Credit Card Integration
| Feature | Description |
|---------|-------------|
| **SimpleFin Support** | Connect external credit cards |
| **Auto-Sync** | Automatic transaction synchronization |
| **Balance Tracking** | Monitor credit card balances |
| **Pocket Linking** | Dedicated pockets for credit card payments |

### 🎨 User Experience
| Feature | Description |
|---------|-------------|
| **Dark Mode** | Beautiful dark theme with auto-detection |
| **Responsive Design** | Optimized for desktop, tablet, and mobile |
| **PWA Support** | Install as a native-like app |
| **Splash Screen** | Branded loading experience |

---

## Screenshots

<p align="center">
  <em>Dashboard and transaction views with dark mode support</em>
</p>

> Screenshots coming soon - the app features a clean, modern interface with support for both light and dark themes.

---

## Quick Start

### Prerequisites
- Docker and Docker Compose (recommended)
- Or: Python 3.9+ for manual installation

### Docker Installation (Recommended)

```bash
# Clone the repository
git clone https://github.com/Nerdykidtech/SimpleCrew.git
cd SimpleCrew

# Start the application
docker-compose up -d --build

# Open in browser
open http://localhost:8080
```

### Manual Installation

```bash
# Clone the repository
git clone https://github.com/Nerdykidtech/SimpleCrew.git
cd SimpleCrew

# Install dependencies
pip install -r requirements.txt

# Create data directory
mkdir -p data

# Run the application
python app.py

# Open in browser
open http://localhost:8080
```

### First-Time Setup

1. Navigate to `http://localhost:8080`
2. Complete the onboarding flow:
   - Select **Crew Banking** as your provider
   - Enter your Crew bearer token
3. Start managing your finances!

> **Getting Your Bearer Token**: Log into [Crew](https://app.trycrew.com), open browser DevTools (F12), go to Network tab, and find the `authorization` header in any API request.

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| **Backend** | Python, Flask |
| **Database** | SQLite |
| **Frontend** | Vanilla JavaScript, HTML5, CSS3 |
| **Containerization** | Docker, Docker Compose |
| **PWA** | Service Worker, Web App Manifest |

---

## Project Structure

```
SimpleCrew/
├── app.py                          # Main Flask application
├── requirements.txt                # Python dependencies
├── Dockerfile                      # Container configuration
├── docker-compose.yml              # Docker Compose setup
│
├── data/                           # Database storage (git-ignored)
│   └── savings_data.db
│
├── static/
│   ├── css/
│   │   ├── main.css                # Core styles & theming
│   │   ├── components.css          # UI components
│   │   ├── navigation.css          # Navigation styles
│   │   ├── modals.css              # Modal dialogs
│   │   └── mobile.css              # Mobile responsive
│   │
│   ├── js/
│   │   ├── api/                    # API layer modules
│   │   │   ├── cards.js
│   │   │   ├── expenses.js
│   │   │   ├── family.js
│   │   │   ├── goals.js
│   │   │   ├── transactions.js
│   │   │   └── credit.js
│   │   │
│   │   ├── ui/                     # UI layer modules
│   │   │   ├── dialogs.js
│   │   │   ├── modals.js
│   │   │   ├── navigation.js
│   │   │   └── rendering.js
│   │   │
│   │   ├── features/               # Feature modules
│   │   │   ├── dragdrop.js
│   │   │   └── groups.js
│   │   │
│   │   ├── utils/                  # Utility functions
│   │   │   ├── formatters.js
│   │   │   └── helpers.js
│   │   │
│   │   ├── state.js                # Global state
│   │   └── app.js                  # Main app initialization
│   │
│   ├── images/
│   │   ├── logo.png                # Light mode logo
│   │   ├── logo_white.png          # Dark mode logo
│   │   ├── 192.png                 # PWA icon
│   │   └── 512.png                 # PWA icon (large)
│   │
│   ├── manifest.json               # PWA manifest
│   └── sw.js                       # Service worker
│
└── templates/
    ├── base.html                   # Base template
    ├── index.html                  # Main dashboard
    ├── onboarding.html             # Setup wizard
    └── partials/
        ├── header.html
        ├── navigation.html
        └── views/                  # Tab views
            ├── activity.html
            ├── expenses.html
            ├── goals.html
            ├── family.html
            ├── cards.html
            └── credit.html
```

---

## API Reference

### Financial Data
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/savings` | GET | Account balances and savings info |
| `/api/transactions` | GET | Transaction history with filtering |
| `/api/transaction/<id>` | GET | Individual transaction details |
| `/api/expenses` | GET | Monthly expenses and bills |
| `/api/goals` | GET | Savings goals and pockets |
| `/api/trends` | GET | Monthly spending trends |

### Account Management
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/subaccounts` | GET | List all subaccounts |
| `/api/family-subaccounts` | GET | All family pockets (grouped) |
| `/api/move-money` | POST | Transfer funds between accounts |
| `/api/create-pocket` | POST | Create new savings pocket |
| `/api/delete-pocket` | POST | Delete savings pocket |
| `/api/create-bill` | POST | Create new expense bill |
| `/api/delete-bill` | POST | Delete expense bill |

### Family & Cards
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/family` | GET | Family member information |
| `/api/cards` | GET | Physical and virtual cards |
| `/api/set-card-spend` | POST | Update card spend source |

### Credit Cards
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/credit/accounts` | GET | Connected credit accounts |
| `/api/credit/transactions/<id>` | GET | Credit card transactions |
| `/api/credit/sync/<id>` | POST | Sync credit card data |

---

## Configuration

### Database-Stored Credentials

All credentials are securely stored in the SQLite database:

| Credential | Configuration |
|------------|---------------|
| **Crew Bearer Token** | Set during onboarding |
| **SimpleFin API Key** | Set in Credit Cards section |

### Environment Variables (Optional)

| Variable | Description | Default |
|----------|-------------|---------|
| `DB_FILE` | Database file path | `savings_data.db` |
| `BEARER_TOKEN` | Legacy token support | - |

---

## Development

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run in debug mode
python app.py
```

### Docker Development

```bash
# Build and run
docker-compose up --build

# View logs
docker-compose logs -f simplecrew

# Rebuild after changes
docker-compose up -d --build
```

---

## Deployment

### Production Recommendations

- Use HTTPS with SSL/TLS termination
- Set up regular database backups
- Configure a reverse proxy (nginx/traefik)
- Monitor API rate limits
- Implement log aggregation

### Docker Production

```bash
# Build for production
docker-compose -f docker-compose.prod.yml up -d

# With custom configuration
docker-compose up -d --build
```

---

## Security

| Aspect | Implementation |
|--------|----------------|
| **Credential Storage** | Encrypted in SQLite database |
| **API Tokens** | Validated before storage |
| **Data Directory** | Excluded from version control |
| **Session Security** | Secure cookie handling |

### Best Practices

- Regularly rotate API credentials
- Backup your `data/` directory
- Keep Docker images updated
- Review access logs periodically

---

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| **Authentication Errors** | Re-enter token via onboarding (delete database) |
| **Database Issues** | Check `data/` directory permissions |
| **API Connection** | Verify network access to `api.trycrew.com` |
| **Mobile Display** | Clear browser cache, check viewport |

### Debug Mode

```python
# In app.py
app.run(host='0.0.0.0', debug=True, port=8080)
```

---

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## Acknowledgments

- Built for use with [Crew Banking](https://trycrew.com)
- Inspired by modern fintech dashboard designs
- Thanks to all contributors and testers

---

<p align="center">
  <strong>SimpleCrew</strong> - Take control of your finances
</p>

<p align="center">
  Made with ❤️ for the Crew community
</p>
