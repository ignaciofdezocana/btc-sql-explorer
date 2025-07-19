#!/usr/bin/env python3
"""
Bitcoin Blockchain SQL Explorer - Beautiful Interactive UI

A beautiful, modern tkinter-based SQL editor for exploring Bitcoin blockchain data.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import duckdb
import pandas as pd
import os
from datetime import datetime
import threading
import json

class BeautifulBitcoinSQLExplorer:
    def __init__(self, root):
        self.root = root
        self.root.title("₿ Bitcoin Blockchain SQL Explorer")
        self.root.geometry("1400x900")
        
        # Set theme and colors
        self.setup_theme()
        
        # Database connection
        self.con = None
        self.connect_database()
        
        # Create UI
        self.create_widgets()
        
        # Load example queries
        self.load_example_queries()
        
        # Apply custom styling
        self.apply_custom_styling()
        
    def setup_theme(self):
        """Setup modern theme and colors"""
        self.colors = {
            'primary': '#f7931a',      # Bitcoin orange
            'secondary': '#2c3e50',    # Dark blue-gray
            'accent': '#3498db',       # Blue
            'success': '#27ae60',      # Green
            'warning': '#f39c12',      # Orange
            'error': '#e74c3c',        # Red
            'light_bg': '#ecf0f1',     # Light gray
            'dark_bg': '#2c3e50',      # Dark background
            'white': '#ffffff',        # White
            'text_dark': '#2c3e50',    # Dark text
            'text_light': '#ffffff',   # Light text
            'border': '#bdc3c7'        # Border color
        }
        
        # Configure ttk style
        style = ttk.Style()
        style.theme_use('clam')  # Use clam theme as base
        
        # Configure custom styles
        style.configure('Title.TLabel', 
                       font=('Segoe UI', 20, 'bold'), 
                       foreground=self.colors['primary'],
                       background=self.colors['white'])
        
        style.configure('Header.TLabel', 
                       font=('Segoe UI', 12, 'bold'), 
                       foreground=self.colors['secondary'],
                       background=self.colors['white'])
        
        style.configure('Bitcoin.TButton',
                       font=('Segoe UI', 10, 'bold'),
                       background=self.colors['primary'],
                       foreground=self.colors['white'],
                       borderwidth=0,
                       focuscolor='none')
        
        style.map('Bitcoin.TButton',
                 background=[('active', self.colors['warning']),
                           ('pressed', self.colors['warning'])])
        
        style.configure('Success.TButton',
                       font=('Segoe UI', 10, 'bold'),
                       background=self.colors['success'],
                       foreground=self.colors['white'],
                       borderwidth=0)
        
        style.configure('Info.TButton',
                       font=('Segoe UI', 9),
                       background=self.colors['accent'],
                       foreground=self.colors['white'],
                       borderwidth=0)
        
        style.configure('Card.TFrame',
                       background=self.colors['white'],
                       relief='solid',
                       borderwidth=1)
        
        style.configure('Status.TLabel',
                       font=('Segoe UI', 9),
                       background=self.colors['light_bg'],
                       foreground=self.colors['text_dark'],
                       relief='sunken',
                       borderwidth=1)
        
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
        """Create the main UI widgets with beautiful styling"""
        # Configure root
        self.root.configure(bg=self.colors['light_bg'])
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        
        # Main container
        main_container = ttk.Frame(self.root, style='Card.TFrame', padding="20")
        main_container.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        main_container.columnconfigure(1, weight=1)
        main_container.rowconfigure(2, weight=3)  # Give more weight to results row
        
        # Header with Bitcoin icon and title
        header_frame = ttk.Frame(main_container, style='Card.TFrame')
        header_frame.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 20))
        
        # Bitcoin icon and title
        title_frame = ttk.Frame(header_frame, style='Card.TFrame')
        title_frame.pack(fill='x', padx=20, pady=15)
        
        bitcoin_icon = ttk.Label(title_frame, text="₿", font=('Segoe UI', 32, 'bold'), 
                                foreground=self.colors['primary'], style='Title.TLabel')
        bitcoin_icon.pack(side='left', padx=(0, 15))
        
        title_label = ttk.Label(title_frame, text="Bitcoin Blockchain SQL Explorer", 
                               style='Title.TLabel')
        title_label.pack(side='left')
        
        # Subtitle
        subtitle_label = ttk.Label(header_frame, text="Explore the Bitcoin blockchain with powerful SQL queries", 
                                  font=('Segoe UI', 10), foreground=self.colors['secondary'])
        subtitle_label.pack(pady=(0, 15))
        
        # Left panel - Example Queries
        left_panel = self.create_left_panel(main_container)
        left_panel.grid(row=1, column=0, rowspan=2, sticky="nsew", padx=(0, 15))
        
        # Center panel - SQL Editor (smaller)
        center_panel = self.create_center_panel(main_container)
        center_panel.grid(row=1, column=1, sticky="nsew", pady=(0, 15))
        
        # Right panel - Results (much larger)
        right_panel = self.create_right_panel(main_container)
        right_panel.grid(row=2, column=1, sticky="nsew")
        
        # Status bar
        self.create_status_bar(main_container)
        
        # Menu bar
        self.create_menu()
        
    def create_left_panel(self, parent):
        """Create the left panel with example queries"""
        panel = ttk.LabelFrame(parent, text="📚 Example Queries", padding="15", 
                              style='Card.TFrame')
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)
        
        # Search box
        search_frame = ttk.Frame(panel, style='Card.TFrame')
        search_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        
        search_label = ttk.Label(search_frame, text="🔍", font=('Segoe UI', 12))
        search_label.pack(side='left', padx=(0, 5))
        
        self.search_var = tk.StringVar()
        self.search_var.trace('w', self.filter_queries)
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var, 
                                font=('Segoe UI', 9), width=20)
        search_entry.pack(side='left', fill='x', expand=True)
        
        # Queries listbox with custom styling
        listbox_frame = ttk.Frame(panel, style='Card.TFrame')
        listbox_frame.grid(row=1, column=0, sticky="nsew")
        listbox_frame.columnconfigure(0, weight=1)
        listbox_frame.rowconfigure(0, weight=1)
        
        self.query_listbox = tk.Listbox(listbox_frame, 
                                       font=('Segoe UI', 10),
                                       bg=self.colors['white'],
                                       fg=self.colors['text_dark'],
                                       selectbackground=self.colors['primary'],
                                       selectforeground=self.colors['white'],
                                       borderwidth=1,
                                       relief='solid',
                                       activestyle='none')
        self.query_listbox.grid(row=0, column=0, sticky="nsew")
        
        # Scrollbar
        query_scrollbar = ttk.Scrollbar(listbox_frame, orient=tk.VERTICAL, 
                                       command=self.query_listbox.yview)
        query_scrollbar.grid(row=0, column=1, sticky="ns")
        self.query_listbox.configure(yscrollcommand=query_scrollbar.set)
        
        # Buttons frame
        buttons_frame = ttk.Frame(panel, style='Card.TFrame')
        buttons_frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        
        load_btn = ttk.Button(buttons_frame, text="📥 Load Query", 
                             command=self.load_selected_query, style='Bitcoin.TButton')
        load_btn.pack(side='left', padx=(0, 5))
        
        clear_btn = ttk.Button(buttons_frame, text="🗑️ Clear", 
                              command=self.clear_editor, style='Info.TButton')
        clear_btn.pack(side='left')
        
        return panel
    
    def create_center_panel(self, parent):
        """Create the center panel with SQL editor"""
        panel = ttk.LabelFrame(parent, text="💻 SQL Editor", padding="15", 
                              style='Card.TFrame')
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(0, weight=1)
        
        # SQL editor with syntax highlighting colors (smaller height)
        editor_frame = ttk.Frame(panel, style='Card.TFrame')
        editor_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 10))
        editor_frame.columnconfigure(0, weight=1)
        editor_frame.rowconfigure(0, weight=1)
        
        self.sql_text = scrolledtext.ScrolledText(
            editor_frame,
            font=('Consolas', 11),
            bg=self.colors['white'],
            fg=self.colors['text_dark'],
            insertbackground=self.colors['primary'],
            selectbackground=self.colors['accent'],
            selectforeground=self.colors['white'],
            borderwidth=1,
            relief='solid',
            padx=10,
            pady=10,
            height=8  # Smaller height for SQL editor
        )
        self.sql_text.grid(row=0, column=0, sticky="nsew")
        
        # Control buttons
        controls_frame = ttk.Frame(panel, style='Card.TFrame')
        controls_frame.grid(row=1, column=0, sticky="ew")
        
        execute_btn = ttk.Button(controls_frame, text="🚀 Execute Query", 
                                command=self.execute_query, style='Success.TButton')
        execute_btn.pack(side='left', padx=(0, 10))
        
        schema_btn = ttk.Button(controls_frame, text="📋 Show Schema", 
                               command=self.show_schema, style='Info.TButton')
        schema_btn.pack(side='left')
        
        return panel
    
    def create_right_panel(self, parent):
        """Create the right panel with results"""
        panel = ttk.LabelFrame(parent, text="📊 Results", padding="15", 
                              style='Card.TFrame')
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(1, weight=1)  # Give maximum weight to results area
        
        # Results info
        self.results_info = ttk.Label(panel, text="No results yet", 
                                     font=('Segoe UI', 9), foreground=self.colors['secondary'])
        self.results_info.grid(row=0, column=0, sticky="w", pady=(0, 10))
        
        # Results treeview (much larger)
        tree_frame = ttk.Frame(panel, style='Card.TFrame')
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        
        # Configure treeview style
        style = ttk.Style()
        style.configure("Treeview",
                       background=self.colors['white'],
                       foreground=self.colors['text_dark'],
                       fieldbackground=self.colors['white'],
                       borderwidth=1,
                       relief='solid')
        style.configure("Treeview.Heading",
                       background=self.colors['primary'],
                       foreground=self.colors['white'],
                       font=('Segoe UI', 9, 'bold'))
        style.map("Treeview",
                 background=[('selected', self.colors['accent'])],
                 foreground=[('selected', self.colors['white'])])
        
        self.results_tree = ttk.Treeview(tree_frame, style="Treeview")
        self.results_tree.grid(row=0, column=0, sticky="nsew")
        
        # Scrollbars
        v_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, 
                                   command=self.results_tree.yview)
        v_scrollbar.grid(row=0, column=1, sticky="ns")
        
        h_scrollbar = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, 
                                   command=self.results_tree.xview)
        h_scrollbar.grid(row=1, column=0, sticky="ew")
        
        self.results_tree.configure(yscrollcommand=v_scrollbar.set, 
                                  xscrollcommand=h_scrollbar.set)
        
        # Export button
        export_btn = ttk.Button(panel, text="📥 Export to CSV", 
                               command=self.save_results, style='Bitcoin.TButton')
        export_btn.grid(row=2, column=0, sticky="w", pady=(10, 0))
        
        return panel
    
    def create_status_bar(self, parent):
        """Create the status bar"""
        self.status_var = tk.StringVar()
        self.status_var.set("Ready to explore the Bitcoin blockchain! 🚀")
        
        status_bar = ttk.Label(parent, textvariable=self.status_var, 
                              style='Status.TLabel', padding="5")
        status_bar.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(15, 0))
    
    def create_menu(self):
        """Create the menu bar"""
        menubar = tk.Menu(self.root, bg=self.colors['white'], fg=self.colors['text_dark'])
        self.root.config(menu=menubar)
        
        # File menu
        file_menu = tk.Menu(menubar, tearoff=0, bg=self.colors['white'], fg=self.colors['text_dark'])
        menubar.add_cascade(label="📁 File", menu=file_menu)
        file_menu.add_command(label="💾 Save Results", command=self.save_results)
        file_menu.add_separator()
        file_menu.add_command(label="🚪 Exit", command=self.root.quit)
        
        # Query menu
        query_menu = tk.Menu(menubar, tearoff=0, bg=self.colors['white'], fg=self.colors['text_dark'])
        menubar.add_cascade(label="🔍 Query", menu=query_menu)
        query_menu.add_command(label="🗑️ Clear Editor", command=self.clear_editor)
        query_menu.add_command(label="📋 Show Schema", command=self.show_schema)
        query_menu.add_separator()
        query_menu.add_command(label="⚡ Quick Stats", command=self.show_quick_stats)
        
        # Help menu
        help_menu = tk.Menu(menubar, tearoff=0, bg=self.colors['white'], fg=self.colors['text_dark'])
        menubar.add_cascade(label="❓ Help", menu=help_menu)
        help_menu.add_command(label="📖 About", command=self.show_about)
        help_menu.add_command(label="🔧 Database Info", command=self.show_database_info)
    
    def filter_queries(self, *args):
        """Filter queries based on search text"""
        search_text = self.search_var.get().lower()
        self.query_listbox.delete(0, tk.END)
        
        for query_name in self.example_queries.keys():
            if search_text in query_name.lower():
                self.query_listbox.insert(tk.END, query_name)
    
    def load_example_queries(self):
        """Load example queries into the listbox"""
        self.example_queries = {
            "📊 Basic Stats": """
SELECT 
    COUNT(*) as total_blocks,
    MIN(number) as first_block,
    MAX(number) as last_block
FROM blocks""",
            
            "💰 Transaction Stats": """
SELECT 
    COUNT(*) as total_transactions,
    COUNT(CASE WHEN is_coinbase THEN 1 END) as coinbase_tx,
    COUNT(CASE WHEN NOT is_coinbase THEN 1 END) as regular_tx
FROM transactions""",
            
            "📈 Daily Transaction Volume": """
SELECT 
    DATE(timestamp 'epoch' + block_timestamp * interval '1 second') as date,
    COUNT(*) as tx_count,
    SUM(output_value) / 100000000.0 as volume_btc
FROM transactions 
GROUP BY DATE(timestamp 'epoch' + block_timestamp * interval '1 second')
ORDER BY date DESC
LIMIT 10""",
            
            "🏆 Largest Transactions": """
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
            
            "📦 Block Size Distribution": """
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
            
            "🕒 Recent Blocks": """
SELECT 
    number,
    hash,
    transaction_count,
    size,
    timestamp
FROM blocks 
ORDER BY number DESC 
LIMIT 10""",
            
            "🌱 Genesis Block": """
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
            
            "📋 Table Schemas": """
SELECT 
    table_name,
    column_name,
    data_type
FROM information_schema.columns 
WHERE table_schema = 'main'
ORDER BY table_name, ordinal_position""",
            
            "⚡ Mining Difficulty": """
SELECT 
    number,
    bits,
    timestamp,
    ROUND(65535.0 / (bits & 0x00FFFFFF), 2) as difficulty
FROM blocks 
ORDER BY number DESC 
LIMIT 20""",
            
            "🎯 Transaction Fees": """
SELECT 
    CASE 
        WHEN fee < 0 THEN 'Negative (Coinbase)'
        WHEN fee = 0 THEN 'Zero'
        WHEN fee < 1000 THEN '< 1000 sats'
        WHEN fee < 10000 THEN '1000-10000 sats'
        ELSE '> 10000 sats'
    END as fee_range,
    COUNT(*) as transaction_count,
    ROUND(AVG(fee), 0) as avg_fee_sats
FROM transactions 
WHERE fee >= 0
GROUP BY fee_range
ORDER BY MIN(fee)"""
        }
        
        for query_name in self.example_queries.keys():
            self.query_listbox.insert(tk.END, query_name)
    
    def load_selected_query(self):
        """Load the selected query into the editor"""
        selection = self.query_listbox.curselection()
        if selection:
            query_name = self.query_listbox.get(selection[0])
            # Remove emoji from query name for lookup
            clean_name = query_name.split(' ', 1)[1] if ' ' in query_name else query_name
            query = self.example_queries.get(query_name, "")
            self.sql_text.delete(1.0, tk.END)
            self.sql_text.insert(1.0, query.strip())
            self.status_var.set(f"Loaded query: {clean_name}")
    
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
            self.status_var.set("⏳ Executing query...")
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
            self.results_info.config(text="✅ Query executed successfully (no results)")
            self.status_var.set("Query completed with no results")
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
        
        # Update info and status
        self.results_info.config(text=f"📊 {len(result)} rows, {len(result.columns)} columns")
        self.status_var.set(f"✅ Query executed successfully in {execution_time:.3f}s")
    
    def _show_error(self, error_msg):
        """Show error message"""
        messagebox.showerror("Query Error", error_msg)
        self.status_var.set("❌ Query failed")
    
    def clear_editor(self):
        """Clear the SQL editor"""
        self.sql_text.delete(1.0, tk.END)
        self.status_var.set("Editor cleared")
    
    def save_results(self):
        """Save current results to CSV file"""
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
                columns = self.results_tree['columns']
                data = []
                for child in children:
                    row_data = self.results_tree.item(child)['values']
                    data.append(row_data)
                
                df = pd.DataFrame(data, columns=columns)
                df.to_csv(filename, index=False)
                messagebox.showinfo("Success", f"Results saved to {filename}")
                self.status_var.set(f"💾 Results exported to {os.path.basename(filename)}")
                
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
            schema_window.title("📋 Database Schema")
            schema_window.geometry("700x500")
            schema_window.configure(bg=self.colors['light_bg'])
            
            # Title
            title_label = ttk.Label(schema_window, text="Database Schema", 
                                   style='Header.TLabel')
            title_label.pack(pady=10)
            
            # Create treeview for schema
            schema_tree = ttk.Treeview(schema_window, columns=('table', 'column', 'type'), 
                                      show='headings', style="Treeview")
            schema_tree.heading('table', text='Table')
            schema_tree.heading('column', text='Column')
            schema_tree.heading('type', text='Type')
            
            schema_tree.column('table', width=150)
            schema_tree.column('column', width=250)
            schema_tree.column('type', width=100)
            
            # Add scrollbar
            scrollbar = ttk.Scrollbar(schema_window, orient=tk.VERTICAL, 
                                    command=schema_tree.yview)
            schema_tree.configure(yscrollcommand=scrollbar.set)
            
            # Pack widgets
            schema_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)
            scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=10)
            
            # Insert data
            for idx, row in result.iterrows():
                schema_tree.insert('', tk.END, values=(row['table_name'], 
                                                      row['column_name'], 
                                                      row['data_type']))
                
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load schema: {e}")
    
    def show_quick_stats(self):
        """Show quick database statistics"""
        if not self.con:
            messagebox.showerror("Error", "No database connection")
            return
        
        try:
            stats_query = """
SELECT 
    (SELECT COUNT(*) FROM blocks) as total_blocks,
    (SELECT COUNT(*) FROM transactions) as total_transactions,
    (SELECT COUNT(*) FROM transaction_inputs) as total_inputs,
    (SELECT COUNT(*) FROM transaction_outputs) as total_outputs,
    (SELECT MIN(number) FROM blocks) as first_block,
    (SELECT MAX(number) FROM blocks) as last_block
            """
            
            result = self.con.execute(stats_query).fetchdf()
            stats = result.iloc[0]
            
            stats_text = f"""
📊 Quick Database Statistics

🔗 Total Blocks: {stats['total_blocks']:,}
💰 Total Transactions: {stats['total_transactions']:,}
📥 Total Inputs: {stats['total_inputs']:,}
📤 Total Outputs: {stats['total_outputs']:,}
🌱 First Block: {stats['first_block']:,}
🏁 Last Block: {stats['last_block']:,}
📏 Block Range: {stats['last_block'] - stats['first_block'] + 1:,} blocks
            """
            
            messagebox.showinfo("Quick Stats", stats_text)
            
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load stats: {e}")
    
    def show_database_info(self):
        """Show database file information"""
        if os.path.exists('bitcoin_blockchain.db'):
            file_size = os.path.getsize('bitcoin_blockchain.db')
            file_size_mb = file_size / (1024 * 1024)
            
            info_text = f"""
🗄️ Database Information

📁 File: bitcoin_blockchain.db
📏 Size: {file_size_mb:.2f} MB
📅 Created: {datetime.fromtimestamp(os.path.getctime('bitcoin_blockchain.db')).strftime('%Y-%m-%d %H:%M:%S')}
🔧 Engine: DuckDB
            """
            
            messagebox.showinfo("Database Info", info_text)
        else:
            messagebox.showwarning("Database Info", "Database file not found")
    
    def show_about(self):
        """Show about dialog"""
        about_text = """
₿ Bitcoin Blockchain SQL Explorer

A beautiful SQL interface for exploring Bitcoin blockchain data.

✨ Features:
• 🎨 Beautiful, modern UI design
• 💻 SQL query editor with syntax highlighting
• 📚 Pre-built example queries
• 📊 Tabular results with auto-sizing
• 💾 Export to CSV functionality
• 📋 Database schema viewer
• ⚡ Quick statistics
• 🔍 Search and filter queries

🗃️ Available Tables:
• blocks - Block information
• transactions - Transaction details
• transaction_inputs - Input details
• transaction_outputs - Output details

🛠️ Built with:
• Python & tkinter
• DuckDB for fast analytics
• Bitcoin orange theme 🧡

🚀 Ready to explore the blockchain!
        """
        messagebox.showinfo("About", about_text)
    
    def apply_custom_styling(self):
        """Apply additional custom styling"""
        # Bind double-click to load queries
        self.query_listbox.bind('<Double-Button-1>', lambda e: self.load_selected_query())
        
        # Add some initial text to the editor
        self.sql_text.insert(1.0, "-- Welcome to Bitcoin Blockchain SQL Explorer!\n-- Start by selecting an example query from the left panel\n-- or write your own SQL query here\n\nSELECT * FROM blocks LIMIT 5;")

def main():
    root = tk.Tk()
    app = BeautifulBitcoinSQLExplorer(root)
    root.mainloop()

if __name__ == "__main__":
    main() 