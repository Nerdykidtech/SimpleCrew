# Simple Banking Dashboard

A modern web application that provides a comprehensive financial dashboard interface, built with Flask and designed to work with banking APIs. This application offers transaction tracking, expense management, savings goals, and family account oversight.

## Features

### 📊 Dashboard Overview
- **Safe-to-Spend Calculator**: Real-time calculation of available spending money
- **Transaction History**: Searchable and filterable transaction list
- **Balance Tracking**: Historical balance data with SQLite storage

### 💰 Financial Management
- **Expense Tracking**: Monthly bill management with automatic reservations
- **Savings Pockets**: Goal-based savings with progress tracking
- **Money Transfers**: Move funds between accounts and pockets
- **Trend Analysis**: Monthly spending and earning insights
- **Credit Card Tracking**: Support for multiple credit card accounts via SimpleFin
  - Automatic transaction syncing
  - Balance management with dedicated pockets
  - Per-account independent sync schedules

### 👨‍👩‍👧‍👦 Family Features
- **Family Accounts**: Manage children and parent accounts
- **Card Management**: Track physical and virtual debit cards
- **Allowance Tracking**: Monitor scheduled allowances

### 📱 Progressive Web App (PWA)
- **Mobile Optimized**: Responsive design with mobile-first approach
- **Offline Capable**: Service worker for offline functionality
- **App-like Experience**: Installable on mobile devices

## Technology Stack

- **Backend**: Python Flask
- **Database**: SQLite
- **Frontend**: Vanilla JavaScript, HTML5, CSS3
- **Containerization**: Docker & Docker Compose
- **PWA**: Service Worker, Web App Manifest

## Quick Start

### Prerequisites
- Docker and Docker Compose installed

### Installation

1. **Clone the repository**
   ```bash
   git clone <your-repo-url>
   cd SimpleCrew
   ```

2. **Start the application**
   ```bash
   docker-compose up -d --build
   ```

3. **Complete onboarding**

   Open your browser and navigate to: `http://localhost:8080`

   On first run, you'll be guided through an onboarding process to:
   - Select your banking provider (Crew or Monzo)
   - Enter your Crew bearer token (stored securely in the database)
   - Configure LunchFlow API key (optional, for credit card tracking)

   All credentials are stored securely in the local SQLite database, not in environment variables.

> **Note**: For existing installations with tokens in `docker-compose.yml`, those will be automatically migrated to the database on first run.

### Manual Setup (without Docker)

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Create data directory**
   ```bash
   mkdir -p data
   ```

3. **Run the application**
   ```bash
   python app.py
   ```

4. **Complete onboarding**

   Navigate to `http://localhost:8080` and follow the onboarding flow to configure your credentials.

## Configuration

### Database-Stored Credentials

As of the latest version, all API credentials are stored securely in the SQLite database through the onboarding flow:
- **Crew Bearer Token**: Configured during first-time setup
- **LunchFlow API Key**: Configured in the Credit Cards section when needed

### Environment Variables (Optional - Backward Compatibility)

Environment variables are still supported for backward compatibility but are **no longer required**:

| Variable | Description | Required |
|----------|-------------|----------|
| `BEARER_TOKEN` | Crew API authentication bearer token | No (configured via onboarding UI) |
| `LUNCHFLOW_API_KEY` | LunchFlow API key for credit card integration | No (configured via Credit Cards UI) |
| `DB_FILE` | SQLite database file path | No (defaults to `savings_data.db`) |

> **Migration**: If you have existing environment variables set, they will be automatically migrated to the database on first run, and the app will continue to work seamlessly.

### API Configuration

The application connects to the Crew API at `https://api.trycrew.com/willow/graphql`. Ensure your tokens have the necessary permissions for:
- Account balance queries
- Transaction history
- Bill management
- Subaccount operations
- Family account access

## Project Structure

```
SimpleCrew/
├── app.py                      # Main Flask application
├── requirements.txt            # Python dependencies
├── Dockerfile                  # Docker container configuration
├── docker-compose.yml.template # Template for Docker Compose setup
├── docker-compose.yml          # Your local config (git-ignored)
├── .gitignore                  # Git ignore rules
├── data/                       # Database storage (git-ignored)
│   └── savings_data.db         # SQLite database
├── static/                     # Static web assets
│   ├── manifest.json           # PWA manifest
│   └── sw.js                   # Service worker
└── templates/
    └── index.html              # Main application template
```

## API Endpoints

### Financial Data
- `GET /api/savings` - Account balances and savings information
- `GET /api/transactions` - Transaction history with filtering
- `GET /api/transaction/<id>` - Individual transaction details
- `GET /api/expenses` - Monthly expenses and bills
- `GET /api/goals` - Savings goals and pockets
- `GET /api/trends` - Monthly spending trends

### Account Management
- `GET /api/subaccounts` - List all subaccounts
- `POST /api/move-money` - Transfer funds between accounts
- `POST /api/create-pocket` - Create new savings pocket
- `POST /api/delete-pocket` - Delete savings pocket
- `POST /api/create-bill` - Create new expense bill
- `POST /api/delete-bill` - Delete expense bill

### Family & Cards
- `GET /api/family` - Family member information
- `GET /api/cards` - Debit card information

### Data Storage
- `GET /api/history` - Historical balance data

## Features in Detail

### Caching System
- Built-in caching with configurable TTL (default: 5 minutes)
- Force refresh capability for real-time data
- Automatic cache invalidation on data modifications

### Transaction Filtering
- Search by description/title
- Date range filtering
- Amount range filtering
- Real-time search suggestions

### Expense Management
- Automatic bill detection and categorization
- Variable bill amount adjustment
- Funding schedule optimization
- Progress tracking with visual indicators

### Mobile Experience
- Touch-optimized interface
- Bottom navigation for mobile
- Responsive design breakpoints
- PWA installation prompts

## Development

### Local Development Setup

1. **Install development dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Enable debug mode**
   ```python
   # In app.py, ensure debug=True
   app.run(host='0.0.0.0', debug=True, port=8080)
   ```

3. **Database initialization**
   The SQLite database is automatically created on first run with the required schema.

### Adding New Features

The application follows a modular structure:
- **Backend**: Add new routes in `app.py`
- **Frontend**: Extend JavaScript functions in `index.html`
- **Styling**: CSS is embedded in the HTML template
- **API Integration**: Use the existing caching decorators for new endpoints

## Deployment

### Docker Deployment (Recommended)

```bash
# Build and start
docker-compose up -d

# View logs
docker-compose logs -f

# Stop services
docker-compose down
```

### Production Considerations

- Use environment-specific configuration files
- Implement proper logging
- Set up SSL/TLS termination
- Configure backup strategy for SQLite database
- Monitor API rate limits and implement retry logic

## Troubleshooting

### Common Issues

1. **Authentication Errors**
   - Complete the onboarding flow to configure your Crew bearer token
   - Check token validity by testing it in the Crew web app
   - If needed, re-enter your token through the onboarding screen (delete database and restart)

2. **Database Issues**
   - Ensure the `data` directory has write permissions
   - Check SQLite file permissions

3. **API Connection Problems**
   - Verify network connectivity to `api.trycrew.com`
   - Check firewall settings

4. **Mobile Display Issues**
   - Clear browser cache
   - Ensure viewport meta tag is present
   - Test on different screen sizes

### Debug Mode

Enable debug mode for detailed error messages:
```python
app.run(host='0.0.0.0', debug=True, port=8080)
```

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Security Notes

- **Credential Storage**
  - All API credentials are stored in the local SQLite database (`data/savings_data.db`)
  - The `data/` directory is excluded from Git to protect your credentials and data
  - Database files are never committed to version control
- **Token Security**
  - Tokens are validated before being stored in the database
  - Bearer tokens are stored securely and used only for API authentication
  - The onboarding flow ensures proper token format and validity
- **User data protection**
  - `.claude/` directory is excluded to protect your development sessions
  - `docker-compose.yml` is in `.gitignore` if you use environment variables
- **Best practices**
  - Regularly rotate API credentials (update through the onboarding flow)
  - Backup your `data/` directory to preserve your database
  - Keep your Docker images updated
  - Consider implementing rate limiting for production use

## Support

For issues and questions:
1. Check the troubleshooting section
2. Review the API documentation
3. Create an issue in the repository
4. Contact the development team

---

**Note**: This application is designed to work with the Crew banking API. Ensure you have proper authorization and comply with all API terms of service.
