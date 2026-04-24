# ST-Pitch (Spatial-Temporal Pitch)

ST-Pitch is a GIS-based database and a Web-based interface for analyzing spatio-temporal data for soccer matches. It provides a comprehensive framework for storing, querying, and visualizing soccer match data, including event occurrences and event sequences.

The tools are designed to facilitate in-depth analysis of soccer matches, enabling users to gain insights into player performance, team strategies, and match dynamics from large volumes of data.


## Features

- **Database Management**: Efficiently store and manage large volumes of soccer match data, including event occurrences and event sequences, using GIS technology. Efficient indexing and query optimization with PostGIS ensure fast access to spatial and temporal data, enabling users to perform complex queries and analyses on soccer match data.
- **Web-Based Visualization**: Visualize the spatio-temporal soccer match data through an interactive web interface, allowing users to explore and analyze data in a user-friendly manner.


## Technologies Used

- **Database**: PostgreSQL with PostGIS extension for spatial data management.
- **Web Framework**: Flask for building the web application.
- **Visualization**: Leaflet.js for interactive maps and data visualization.

## Installation and Usage

The following steps are based on Ubuntu Linux 24.04 LTS. Please adjust the commands accordingly for other environments.

1. Clone the repository:
   ```bash
    git clone https://github.com/alab-forge/ST-Pitch.git
    cd ST-Pitch
   ```
1. Set up the PostgreSQL database and PostGIS extension.
   ```bash
    sudo apt-get install postgresql postgresql-contrib postgis
    sudo systemctl enable --now postgresql
   ```
1. Create a new database.
   Start the PostgreSQL shell (psql) as the postgres user:
   ```bash
    sudo -u postgres psql
   ```
   In the PostgreSQL shell, run:
   ```sql
    CREATE USER your_user WITH PASSWORD 'your_password'; -- Replace with your desired username and password
    CREATE DATABASE your_db OWNER your_user; -- Replace with your desired database name and owner
    \c your_db -- Connect to the newly created database
    CREATE EXTENSION postgis;
    \q -- Exit the PostgreSQL shell
   ```
1. Create a virtual environment and install the required Python packages.
   ```bash
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
   ```
1. Setup the database configuration using the `.env` file.
   The project uses a `.env` file for database and application configuration. To set up your environment:
   
   1. Create a symbolic link from `dot.env` to `.env`:
   ```bash
    ln -s dot.env .env
   ```
   
   2. Edit `.env` and replace the placeholders with your actual values:
   ```bash
    # Database Configuration
    DB_NAME=your_db
    DB_USER=your_user
    DB_PASSWORD=your_password
    DB_HOST=localhost
    DB_PORT=5432

    # Flask Configuration
    SECRET_KEY=your_secret_key
   ```
   
   The `.env` file is automatically loaded when the application starts and is excluded from version control for security reasons.

2. Run the Flask application.
   ```bash
   ./run.sh
   ```
3. Access the web application by navigating to `http://localhost:5000` in your web browser.

### Note

- Ensure that you have the necessary permissions and configurations for the PostgreSQL database and PostGIS extension to run the application successfully.
- The name of the database, user, and password used in the example above are placeholders. Please replace them with your actual database credentials.
- If you are deploying the application on a public server, consider using a WSGI server such as uWSGI or Gunicorn to serve the Flask application. Additionally, using a reverse proxy server like Nginx can enhance security and performance. 

## Contributing

Contributions to ST-Pitch are welcome! If you have any ideas, suggestions, or improvements, please feel free to submit a pull request or open an issue on the GitHub repository.

## License

ST-Pitch is licensed under the MIT License. See the [LICENSE](LICENSE) file for more details.

