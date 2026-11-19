# FoodShare — food donation coordination website

FoodShare is a local website that helps community groups record food donors, available donations, receiving organizations, and food requests. It supports guarded allocation, distribution status tracking, a community dashboard, and donor-wise reports. It simulates coordination only; it does not arrange delivery, certify food safety, or process payments.

## Run the website

1. Install Python 3.10 or newer.
2. Open a terminal in this project folder.
3. Install the web framework:

   ```bash
   python -m pip install -r requirements.txt
   ```

4. Start FoodShare:

   ```bash
   python app.py
   ```

5. Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser and create an account.

The database is `foodshare.db` in this folder. The website creates its account table on startup and preserves existing donor, organization, donation, request, and allocation records from the earlier desktop version. Keep the database file to preserve your records. The original Tkinter desktop edition can still be started with `python main.py`.

## Website pages

- **Home:** dashboard metrics, recent donations, and open community requests.
- **Donor partners / Receiving organizations:** register and review partner records.
- **Food donations:** record donor, category, whole-number quantity, unit, preparation time, and use-by time.
- **Food requests:** record organization needs and see allocated progress.
- **Allocate food:** match a donation with a request and record a successful allocation.
- **Reports:** view total donations, available quantity, pending and completed requests, distributed quantity, expired donations, and donor-wise history.

## Example workflow

1. Register a donor with ID `D001` and an organization with ID `O001`.
2. Add donation `D101` for 50 Packets of Rice Meals.
3. Create request `R101` for 30 Packets of Rice Meals.
4. Allocate 30. The request becomes completed, and 20 packets remain on the donation.
5. Mark the allocation as distributed. The 30 packets then count toward the distributed-food report.

## Allocation rules

- IDs must be unique within donors, organizations, donations, and requests.
- Quantities must be positive whole numbers; allocation cannot exceed available food or the request's outstanding need.
- Food category and unit must match. Expired donations cannot be allocated.
- Use-by time must follow preparation time. Enter dates in local time as `YYYY-MM-DD HH:MM` (24-hour clock).
- Requests complete when the full requested quantity is allocated. Allocations remain in the history with their distribution status and timestamps.
- Account passwords are stored as password hashes, not plain text. Accounts share the coordination records in this local simulation.

## Project files

- `app.py` — Flask website and application logic.
- `templates/` — website pages.
- `static/style.css` — responsive styling.
- `foodshare.db` — local SQLite database, created automatically when the app first starts.
