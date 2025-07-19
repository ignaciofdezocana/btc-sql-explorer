#!/usr/bin/env python3
"""
Bitcoin Blockchain SQL Explorer - Simple Interactive UI

A simple tkinter-based SQL editor for exploring Bitcoin blockchain data.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import duckdb
import pandas as pd
import os
from datetime import datetime
import threading

class BitcoinSQLExplorer:
    def __init__(self, root):
        self.root = root
        self.root.title("₿ Bitcoin Blockchain SQL Explorer")
        self.root.geometry("1200x800")
        
        # Database connection
        self.con = None
        self.connect_database()
        
        # Create UI
        self.create_widgets()
        
        # Load example queries
        self.load_example_queries()
        
    def connect_database(self):
        """Connect to the DuckDB database"""
        if not os.path.exists('bitcoin_blockchain.db'):
            messagebox.showerror("Error", "Database file 'bitcoin_blockchain.db' not found.\nPlease run btc_duckdb_setup.py first.")
            return False
        
        try:
            self.con = duckdb.connect('bitcoin_blockchain.db')
            return True
        except Exception as e:
            messagebox.showerror("Error", f"Failed to connect to database: {e}")
            return False
    
    def create_widgets(self):
        """Create the main UI widgets"""
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Configure grid weights
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(2, weight=1)
        
        # Title
        title_label = ttk.Label(main_frame, text="₿ Bitcoin Blockchain SQL Explorer", 
                               font=("Arial", 16, "bold"))
        title_label.grid(row=0, column=0, columnspan=3, pady=(0, 10))
        
        # Left panel for example queries
        left_frame = ttk.LabelFrame(main_frame, text="Example Queries", padding="5")
        left_frame.grid(row=1, column=0, rowspan=2, sticky="nsew", padx=(0, 10))
        left_frame.columnconfigure(0, weight=1)
        left_frame.rowconfigure(1, weight=1)
        
        # Example queries listbox
        self.query_listbox = tk.Listbox(left_frame, width=30)
        self.query_listbox.grid(row=0, column=0, sticky="nsew")
        
        # Scrollbar for listbox
        query_scrollbar = ttk.Scrollbar(left_frame, orient=tk.VERTICAL, command=self.query_listbox.yview)
        query_scrollbar.grid(row=0, column=1, sticky="ns")
        self.query_listbox.configure(yscrollcommand=query_scrollbar.set)
        
        # Load query button
        load_query_btn = ttk.Button(left_frame, text="Load Query", command=self.load_selected_query)
        load_query_btn.grid(row=1, column=0, pady=(5, 0))
        
        # SQL Editor frame
        sql_frame = ttk.LabelFrame(main_frame, text="SQL Editor", padding="5")
        sql_frame.grid(row=1, column=1, sticky="nsew")
        sql_frame.columnconfigure(0, weight=1)
        sql_frame.rowconfigure(1, weight=1)
        
        # SQL text area
        self.sql_text = scrolledtext.ScrolledText(sql_frame, height=10, width=60, font=("Consolas", 10))
        self.sql_text.grid(row=0, column=0, sticky="nsew", pady=(0, 5))
        
        # Execute button
        execute_btn = ttk.Button(sql_frame, text="Execute Query", command=self.execute_query)
        execute_btn.grid(row=1, column=0, pady=(0, 5))
        
        # Results frame
        results_frame = ttk.LabelFrame(main_frame, text="Results", padding="5")
        results_frame.grid(row=2, column=1, sticky="nsew")
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(1, weight=1)
        
        # Results treeview
        self.results_tree = ttk.Treeview(results_frame)
        self.results_tree.grid(row=0, column=0, sticky="nsew")
        
        # Scrollbars for results
        results_v_scrollbar = ttk.Scrollbar(results_frame, orient=tk.VERTICAL, command=self.results_tree.yview)
        results_v_scrollbar.grid(row=0, column=1, sticky="ns")
        
        results_h_scrollbar = ttk.Scrollbar(results_frame, orient=tk.HORIZONTAL, command=self.results_tree.xview)
        results_h_scrollbar.grid(row=1, column=0, sticky="ew")
        
        self.results_tree.configure(yscrollcommand=results_v_scrollbar.set, xscrollcommand=results_h_scrollbar.set)
        
        # Status bar
        self.status_var = tk.StringVar()
        self.status_var.set("Ready")
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN)
        status_bar.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        
        # Menu bar
        self.create_menu()
        
        # Bind events
        self.query_listbox.bind('<Double-Button-1>', lambda e: self.load_selected_query())
        
    def create_menu(self):
        """Create the menu bar"""
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        # File menu
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Save Results", command=self.save_results)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        
        # Query menu
        query_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Query", menu=query_menu)
        query_menu.add_command(label="Clear Editor", command=self.clear_editor)
        query_menu.add_command(label="Show Table Schema", command=self.show_schema)
        
        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self.show_about)
    
    def load_example_queries(self):
        """Load example queries into the listbox"""
        self.example_queries = {
            "Basic Stats": """
SELECT 
    COUNT(*) as total_blocks,
    MIN(number) as first_block,
    MAX(number) as last_block
FROM blocks""",
            
            "Transaction Stats": """
SELECT 
    COUNT(*) as total_transactions,
    COUNT(CASE WHEN is_coinbase THEN 1 END) as coinbase_tx,
    COUNT(CASE WHEN NOT is_coinbase THEN 1 END) as regular_tx
FROM transactions""",
            
            "Daily Transaction Volume": """
SELECT 
    DATE(timestamp 'epoch' + block_timestamp * interval '1 second') as date,
    COUNT(*) as tx_count,
    SUM(output_value) / 100000000.0 as volume_btc
FROM transactions 
GROUP BY DATE(timestamp 'epoch' + block_timestamp * interval '1 second')
ORDER BY date DESC
LIMIT 10""",
            
            "Largest Transactions": """
SELECT 
    hash,
    ROUND(output_value / 100000000.0, 2) as output_value_btc,
    input_count,
    output_count,
    is_coinbase,
    block_number
FROM transactions 
ORDER BY output_value DESC 
LIMIT 10""",
            
            "Block Size Distribution": """
SELECT 
    CASE 
        WHEN size < 1000 THEN '< 1KB'
        WHEN size < 10000 THEN '1-10KB'
        WHEN size < 100000 THEN '10-100KB'
        WHEN size < 1000000 THEN '100KB-1MB'
        ELSE '> 1MB'
    END as size_range,
    COUNT(*) as block_count,
    ROUND(AVG(size), 0) as avg_size
FROM blocks 
GROUP BY size_range
ORDER BY MIN(size)""",
            
            "Recent Blocks": """
SELECT 
    number,
    hash,
    transaction_count,
    size,
    timestamp
FROM blocks 
ORDER BY number DESC 
LIMIT 10""",
            
            "Genesis Block": """
SELECT 
    b.number,
    b.hash,
    b.timestamp,
    b.transaction_count,
    b.size,
    t.hash as coinbase_tx_hash,
    ROUND(t.output_value / 100000000.0, 2) as coinbase_reward_btc
FROM blocks b
JOIN transactions t ON b.hash = t.block_hash
WHERE b.number = 0 AND t.is_coinbase = true""",
            
            "Table Schemas": """
SELECT 
    table_name,
    column_name,
    data_type
FROM information_schema.columns 
WHERE table_schema = 'main'
ORDER BY table_name, ordinal_position"""
        }
        
        for query_name in self.example_queries.keys():
            self.query_listbox.insert(tk.END, query_name)
    
    def load_selected_query(self):
        """Load the selected query into the editor"""
        selection = self.query_listbox.curselection()
        if selection:
            query_name = self.query_listbox.get(selection[0])
            query = self.example_queries.get(query_name, "")
            self.sql_text.delete(1.0, tk.END)
            self.sql_text.insert(1.0, query.strip())
    
    def execute_query(self):
        """Execute the SQL query and display results"""
        if not self.con:
            messagebox.showerror("Error", "No database connection")
            return
        
        query = self.sql_text.get(1.0, tk.END).strip()
        if not query:
            messagebox.showwarning("Warning", "Please enter a SQL query")
            return
        
        # Run query in a separate thread to avoid blocking UI
        threading.Thread(target=self._execute_query_thread, args=(query,), daemon=True).start()
    
    def _execute_query_thread(self, query):
        """Execute query in a separate thread"""
        try:
            self.status_var.set("Executing query...")
            self.root.update_idletasks()
            
            start_time = datetime.now()
            result = self.con.execute(query).fetchdf()
            execution_time = (datetime.now() - start_time).total_seconds()
            
            # Update UI in main thread
            self.root.after(0, lambda: self._display_results(result, execution_time))
            
        except Exception as e:
            self.root.after(0, lambda: self._show_error(str(e)))
    
    def _display_results(self, result, execution_time):
        """Display query results in the treeview"""
        # Clear existing results
        for item in self.results_tree.get_children():
            self.results_tree.delete(item)
        
        if result.empty:
            self.status_var.set("Query executed successfully (no results)")
            return
        
        # Configure columns
        self.results_tree['columns'] = list(result.columns)
        self.results_tree['show'] = 'headings'
        
        # Set column headings
        for col in result.columns:
            self.results_tree.heading(col, text=col)
            # Adjust column width based on content
            max_width = max(len(str(col)), 
                          result[col].astype(str).str.len().max())
            width = min(max_width * 10, 200)  # Cap at 200 pixels
            self.results_tree.column(col, width=width)
        
        # Insert data
        for idx, row in result.iterrows():
            values = [str(val) if val is not None else '' for val in row.values]
            self.results_tree.insert('', tk.END, values=values)
        
        self.status_var.set(f"Query executed successfully in {execution_time:.3f}s - {len(result)} rows, {len(result.columns)} columns")
    
    def _show_error(self, error_msg):
        """Show error message"""
        messagebox.showerror("Query Error", error_msg)
        self.status_var.set("Query failed")
    
    def clear_editor(self):
        """Clear the SQL editor"""
        self.sql_text.delete(1.0, tk.END)
    
    def save_results(self):
        """Save current results to CSV file"""
        # Get current results from treeview
        children = self.results_tree.get_children()
        if not children:
            messagebox.showwarning("Warning", "No results to save")
            return
        
        filename = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        
        if filename:
            try:
                # Extract data from treeview
                columns = self.results_tree['columns']
                data = []
                for child in children:
                    row_data = self.results_tree.item(child)['values']
                    data.append(row_data)
                
                # Create DataFrame and save
                df = pd.DataFrame(data, columns=columns)
                df.to_csv(filename, index=False)
                messagebox.showinfo("Success", f"Results saved to {filename}")
                
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save file: {e}")
    
    def show_schema(self):
        """Show table schema information"""
        if not self.con:
            messagebox.showerror("Error", "No database connection")
            return
        
        try:
            schema_query = """
SELECT 
    table_name,
    column_name,
    data_type
FROM information_schema.columns 
WHERE table_schema = 'main'
ORDER BY table_name, ordinal_position
            """
            
            result = self.con.execute(schema_query).fetchdf()
            
            # Create a new window to show schema
            schema_window = tk.Toplevel(self.root)
            schema_window.title("Database Schema")
            schema_window.geometry("600x400")
            
            # Create treeview for schema
            schema_tree = ttk.Treeview(schema_window, columns=('table', 'column', 'type'), show='headings')
            schema_tree.heading('table', text='Table')
            schema_tree.heading('column', text='Column')
            schema_tree.heading('type', text='Type')
            
            schema_tree.column('table', width=150)
            schema_tree.column('column', width=200)
            schema_tree.column('type', width=100)
            
            # Add scrollbar
            scrollbar = ttk.Scrollbar(schema_window, orient=tk.VERTICAL, command=schema_tree.yview)
            schema_tree.configure(yscrollcommand=scrollbar.set)
            
            # Pack widgets
            schema_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
            
            # Insert data
            for idx, row in result.iterrows():
                schema_tree.insert('', tk.END, values=(row['table_name'], row['column_name'], row['data_type']))
                
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load schema: {e}")
    
    def show_about(self):
        """Show about dialog"""
        about_text = """
Bitcoin Blockchain SQL Explorer

A simple SQL interface for exploring Bitcoin blockchain data.

Features:
• SQL query editor
• Example queries
• Tabular results view
• Export to CSV
• Database schema viewer

Available tables:
• blocks - Block information
• transactions - Transaction details
• transaction_inputs - Input details
• transaction_outputs - Output details

Built with Python, tkinter, and DuckDB
        """
        messagebox.showinfo("About", about_text)

def main():
    root = tk.Tk()
    app = BitcoinSQLExplorer(root)
    root.mainloop()

if __name__ == "__main__":
    main() 