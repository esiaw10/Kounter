# Kounter — Starter

This is the first working foundation of the Ghana provision-shop POS.

## 1. Install Python
Use Python 3.11+.

## 2. Create and activate a virtual environment

Windows:
    python -m venv venv
    venv\Scripts\activate

Mac/Linux:
    python3 -m venv venv
    source venv/bin/activate

## 3. Install dependencies

    pip install -r requirements.txt

## 4. Create database tables

    python manage.py makemigrations
    python manage.py migrate

## 5. Create administrator

    python manage.py createsuperuser

## 6. Start the server

    python manage.py runserver

Open http://127.0.0.1:8000/

Admin: http://127.0.0.1:8000/admin/

## What works in this starter

- Store database
- User roles
- Products and categories
- Inventory quantity
- Low-stock threshold
- Search
- Alphabetical grouping
- POS minus button
- Automatic stock deduction
- Sale records
- Stock movement records
- Ghana timezone
- Today's sales total

Next development step: proper store-management screens, product/restock screens, quantity selling, date-range reports, permissions, and a polished mobile-friendly interface.
