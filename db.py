import config
import mysql.connector

class Database:

    def __init__(self):

        connect_kwargs = {
            "user": config.DB_USER,
            "password": config.DB_PASSWORD,
            "database": config.DB_NAME,
        }
        if getattr(config, "DB_SOCKET", None):
            connect_kwargs["unix_socket"] = config.DB_SOCKET
        else:
            connect_kwargs["host"] = config.DB_HOST
            if getattr(config, "DB_PORT", None):
                connect_kwargs["port"] = config.DB_PORT

        self.conn = mysql.connector.connect(**connect_kwargs)

        self.cursor = self.conn.cursor(dictionary=True)

    def select(self, table, where=None):

        query = f"SELECT * FROM {table}"
        values = []

        if where:

            conditions = []

            for column, value in where.items():

                conditions.append(f"{column}=%s")
                values.append(value)

            query += " WHERE " + " AND ".join(conditions)

        self.cursor.execute(query, values)

        return self.cursor.fetchall()

    def row(self, table, where=None):

        query = f"SELECT * FROM {table}"
        values = []

        if where:

            conditions = []

            for column, value in where.items():

                conditions.append(f"{column}=%s")
                values.append(value)

            query += " WHERE " + " AND ".join(conditions)

        query += " LIMIT 1"

        self.cursor.execute(query, values)

        return self.cursor.fetchone()

    def insert(self, table, data):

        columns = ", ".join(data.keys())
        placeholders = ", ".join(["%s"] * len(data))

        query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"

        self.cursor.execute(query, list(data.values()))
        self.conn.commit()

        return self.cursor.lastrowid

    def update(self, table, data, where):

        set_clause = ", ".join([f"{column}=%s" for column in data])
        values = list(data.values())

        conditions = " AND ".join([f"{column}=%s" for column in where])
        values += list(where.values())

        query = f"UPDATE {table} SET {set_clause} WHERE {conditions}"

        self.cursor.execute(query, values)
        self.conn.commit()

        return self.cursor.rowcount

    def delete(self, table, where):

        conditions = " AND ".join([f"{column}=%s" for column in where])

        query = f"DELETE FROM {table} WHERE {conditions}"

        self.cursor.execute(query, list(where.values()))
        self.conn.commit()

        return self.cursor.rowcount

    def execute(self, query, values=None):

        self.cursor.execute(query, values or [])
        self.conn.commit()

    def close(self):

        self.cursor.close()
        self.conn.close()