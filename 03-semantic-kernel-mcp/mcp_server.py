# This file has been originally authored by https://github.com/microsoft/OpenAIWorkshop/tree/main/agentic_ai/backend_services

"""
This module implements a Model Context Protocol (MCP) server for Contoso's customer management system.
It provides a set of tools for accessing and managing customer data, billing information,
support tickets, and other customer-related functionalities through a unified API interface.

The server uses FastMCP to expose these capabilities as tools that can be consumed by
AI agents or other services. All data is stored in an SQLite database.
n originally authored by https://github.com/microsoft/OpenAIWorkshop/tree/main/agentic_ai/backend_services
"""

from fastmcp import FastMCP  
from typing import List, Optional, Dict, Any  
from pydantic import BaseModel, Field  
import sqlite3, os, json, math, asyncio, logging  
from datetime import datetime  
from dotenv import load_dotenv  
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Mount, Route
from mcp.server import Server
from mcp.server.sse import SseServerTransport
# Load environment variables from .env file for configuration
load_dotenv()

# Initialize the FastMCP server with a name and instructions for AI agents
# This exposes our customer management tools through a unified API interface
mcp = FastMCP(  
    name="Contoso Customer API as Tools",  
    instructions=(  
        "All customer, billing and knowledge data is accessible ONLY via the declared "  
        "tools below. Return values follow the pydantic schemas. Always call the most "  
        "specific tool that answers the user's question."  
    ),  
)  

from fastmcp import FastMCP  
from typing import List, Optional, Dict, Any  
from pydantic import BaseModel, Field  
import sqlite3, os, json, math, asyncio, logging  
from datetime import datetime  
from dotenv import load_dotenv  
import uvicorn
load_dotenv()

mcp = FastMCP(  
    name="Contoso Customer API as Tools",  
    instructions=(  
        "All customer, billing and knowledge data is accessible ONLY via the declared "  
        "tools below.  Return values follow the pydantic schemas Always call the most "  
        "specific tool that answers the user’s question."  
    ),  
)  
  
# Path to SQLite database file
DB_PATH = "contoso.db"
  
def get_db() -> sqlite3.Connection:
    """
    Creates and returns a new SQLite database connection with Row factory enabled.
    This ensures that database rows can be accessed both by index and column name.
    """
    db = sqlite3.connect(DB_PATH)  
    db.row_factory = sqlite3.Row
    return db
  
# Initialize Azure OpenAI for embeddings, with fallback for development/testing
try:  
    from openai import AzureOpenAI  
  
    # Create Azure OpenAI client for embeddings
    _client = AzureOpenAI(  
        azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT", ""),  # Use empty string as default
        api_key=os.getenv("AZURE_OPENAI_API_KEY", ""),         # Use empty string as default
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", ""), # Use empty string as default
    )  
    _emb_model = os.getenv("AZURE_OPENAI_EMBEDDING_DEPLOYMENT_NAME", "")  # Use empty string as default
  
    def get_embedding(text: str) -> List[float]:  
        """
        Generate embeddings for given text using Azure OpenAI.
        Args:
            text: The input text to generate embeddings for
        Returns:
            A list of floats representing the text embedding
        """
        text = text.replace("\n", " ")  # Normalize text by removing newlines
        return _client.embeddings.create(input=[text], model=_emb_model).data[0].embedding  
  
except Exception:  # pragma: no cover  
    def get_embedding(text: str) -> List[float]:  
        """
        Fallback function that returns a zero vector when Azure OpenAI is not configured.
        This is useful for development and testing scenarios.
        """
        return [0.0] * 1536  # 1536-dimensional zero vector
  
  
def cosine_similarity(vec1, vec2):  
    """
    Calculate the cosine similarity between two vectors.
    This is used to measure the similarity between text embeddings.
    
    Args:
        vec1: First vector
        vec2: Second vector
    Returns:
        Float between -1 and 1, where 1 means vectors are identical,
        0 means they are orthogonal, and -1 means they point in opposite directions.
    """
    dot = sum(a * b for a, b in zip(vec1, vec2))  
    norm1 = math.sqrt(sum(a * a for a in vec1))  
    norm2 = math.sqrt(sum(b * b for b in vec2))  
    return dot / (norm1 * norm2) if norm1 and norm2 else 0.0  
  
  
##############################################################################  
#                              Pydantic MODELS                               #  
##############################################################################  
"""
Data models for the customer management system.
These Pydantic models define the structure and validation rules for all data
that flows through the API. They ensure type safety and provide automatic validation.
"""

class CustomerSummary(BaseModel):  
    """
    Basic customer information model used for list views and summaries.
    Contains only essential customer identification and status information.
    """
    customer_id: int  
    first_name: str  
    last_name: str  
    email: str  
    loyalty_level: str  
  
  
class CustomerDetail(BaseModel):  
    """
    Detailed customer information model.
    Extends CustomerSummary with additional contact information and a list of
    associated subscriptions.
    """
    customer_id: int  
    first_name: str  
    last_name: str  
    email: str  
    phone: Optional[str]  
    address: Optional[str]  
    loyalty_level: str  
    subscriptions: List[dict]  
  
  
class Payment(BaseModel):  
    """
    Payment transaction record model.
    Represents a single payment made towards an invoice.
    """
    payment_id: int  
    payment_date: Optional[str]  # Date can be None for pending payments
    amount: float  
    method: str  # e.g., 'credit_card', 'bank_transfer', etc.
    status: str  # e.g., 'successful', 'pending', 'failed'
  
  
class Invoice(BaseModel):  
    """
    Customer invoice model.
    Represents a billing invoice with its payment history and current outstanding balance.
    """
    invoice_id: int  
    invoice_date: str  
    amount: float  
    description: str  
    due_date: str  
    payments: List[Payment]  # All payments made towards this invoice
    outstanding: float      # Remaining amount to be paid
  
  
class ServiceIncident(BaseModel):  
    """
    Service incident record model.
    Tracks service disruptions or issues affecting a customer's subscription.
    """
    incident_id: int  
    incident_date: str  
    description: str  
    resolution_status: str  # e.g., 'resolved', 'pending', 'in_progress'
  
  
class SubscriptionDetail(BaseModel):  
    """
    Detailed subscription information model.
    Contains all information about a customer's service subscription including
    associated product details, billing history, and service incidents.
    """
    subscription_id: int  
    product_id: int  
    start_date: str  
    end_date: str  
    status: str                     # e.g., 'active', 'suspended', 'cancelled'
    roaming_enabled: int            # Boolean as integer (0/1)
    service_status: str             # e.g., 'running', 'maintenance', 'down'
    speed_tier: Optional[str]       # For internet services
    data_cap_gb: Optional[int]     # For internet/mobile services
    autopay_enabled: int           # Boolean as integer (0/1)
    product_name: str  
    product_description: Optional[str]  
    category: Optional[str]         # e.g., 'mobile', 'internet', 'tv'
    monthly_fee: Optional[float]  
    invoices: List[Invoice]         # Billing history
    service_incidents: List[ServiceIncident]  # Service history
  
  
class Promotion(BaseModel):  
    """
    Promotional offer model.
    Represents a time-limited promotional offer for specific products
    with eligibility criteria and discount information.
    """
    promotion_id: int  
    product_id: int  
    name: str  
    description: str  
    eligibility_criteria: Optional[str]  # e.g., 'loyalty_level = "gold"'
    start_date: str  
    end_date: str  
    discount_percent: Optional[int]  
  
  
class KBSearchParams(BaseModel):  
    """
    Knowledge base search parameters model.
    Used to define search criteria for querying the knowledge base.
    """
    query: str = Field(..., description="natural language query")  
    topk: Optional[int] = Field(3, description="Number of top documents to return")  
  
  
class KBDoc(BaseModel):  
    """
    Knowledge base document model.
    Represents a document in the knowledge base containing
    policies, procedures, or other reference information.
    """
    title: str  
    doc_type: str     # e.g., 'policy', 'procedure', 'faq'
    content: str      # The actual document content
  
  
class SecurityLog(BaseModel):  
    """
    Security event log model.
    Records security-related events such as account lockouts,
    password changes, or suspicious activity.
    """
    log_id: int  
    event_type: str        # e.g., 'account_locked', 'login_attempt'
    event_timestamp: str  
    description: str  
  
  
class Order(BaseModel):  
    """
    Customer order model.
    Represents a product or service order placed by a customer.
    """
    order_id: int  
    order_date: str  
    product_name: str  
    amount: float  
    order_status: str     # e.g., 'pending', 'completed', 'cancelled'
  
  
class DataUsageRecord(BaseModel):  
    """
    Service usage record model.
    Tracks daily usage metrics for mobile/internet services.
    """
    usage_date: str  
    data_used_mb: int     # Mobile data or internet usage
    voice_minutes: int    # Voice call duration
    sms_count: int       # Number of text messages sent
  
  
class SupportTicket(BaseModel):  
    """
    Customer support ticket model.
    Represents a customer service request or issue report with
    tracking information and current status.
    """
    ticket_id: int  
    subscription_id: int            # Associated subscription
    category: str                  # e.g., 'technical', 'billing', 'account'
    opened_at: str                 # Ticket creation timestamp
    closed_at: Optional[str]       # Resolution timestamp
    status: str                    # e.g., 'open', 'in_progress', 'closed'
    priority: str                  # e.g., 'high', 'medium', 'low'
    subject: str                   # Brief issue description
    description: str               # Detailed issue description
    cs_agent: str                  # Assigned customer service agent
  
  
class SubscriptionUpdateRequest(BaseModel):  
    """
    Subscription modification request model.
    Used to update one or more attributes of an existing subscription.
    All fields are optional to allow partial updates.
    """
    roaming_enabled: Optional[int] = None       # Toggle roaming (0/1)
    status: Optional[str] = None               # Change subscription status
    service_status: Optional[str] = None       # Change service state
    product_id: Optional[int] = None           # Change product/plan
    start_date: Optional[str] = None           # Modify contract start
    end_date: Optional[str] = None             # Modify contract end
    autopay_enabled: Optional[int] = None      # Toggle autopay (0/1)
    speed_tier: Optional[str] = None           # Change internet speed
    data_cap_gb: Optional[int] = None         # Modify data limit
  
  
# ─── Parameter Models for API Endpoints ───────────────────────────────────────────
"""
Simple parameter models used to validate input parameters
for various API endpoints. These ensure type safety and
proper parameter validation for API calls.
"""

class CustomerIdParam(BaseModel):  
    """Parameter model for endpoints that require a customer ID."""
    customer_id: int  
  
  
class SubscriptionIdParam(BaseModel):  
    """Parameter model for endpoints that require a subscription ID."""
    subscription_id: int  
  
  
class InvoiceIdParam(BaseModel):  
    """Parameter model for endpoints that require an invoice ID."""
    invoice_id: int  
  
  
##############################################################################  
#                               TOOL ENDPOINTS                               #  
##############################################################################  
"""
API endpoint tools exposed through the MCP server.
Each function is decorated with @mcp.tool to expose it as a callable tool
with a specific description. These tools provide the core functionality
of the customer management system.
"""

@mcp.tool(description="List all customers with basic info")  
def get_all_customers() -> List[CustomerSummary]:  
    """
    Retrieve a list of all customers with their basic information.
    
    Returns:
        List[CustomerSummary]: A list of customer summaries containing
        basic identification and status information for each customer.
    """
    db = get_db()  
    # Query only the essential customer fields
    rows = db.execute(  
        "SELECT customer_id, first_name, last_name, email, loyalty_level FROM Customers"  
    ).fetchall()  
    db.close()  
    # Convert each database row to a CustomerSummary model instance
    return [CustomerSummary(**dict(r)) for r in rows]  
  
  
@mcp.tool(description="Get a full customer profile including their subscriptions")  
def get_customer_detail(params: CustomerIdParam) -> CustomerDetail:  
    """
    Retrieve detailed information about a specific customer including all
    their active subscriptions.
    
    Args:
        params: CustomerIdParam containing the customer_id to look up
        
    Returns:
        CustomerDetail: Detailed customer profile including subscription list
        
    Raises:
        ValueError: If the customer ID is not found in the database
    """
    db = get_db()  
    # Get full customer record
    cust = db.execute(  
        "SELECT * FROM Customers WHERE customer_id = ?", (params.customer_id,)  
    ).fetchone()  
    if not cust:  
        db.close()  
        raise ValueError(f"Customer {params.customer_id} not found")  
    # Get all subscriptions for this customer
    subs = db.execute(  
        "SELECT * FROM Subscriptions WHERE customer_id = ?", (params.customer_id,)  
    ).fetchall()  
    db.close()  
    # Combine customer data with their subscriptions
    return CustomerDetail(**dict(cust), subscriptions=[dict(s) for s in subs])  
  
  
@mcp.tool(  
    description=(  
        "Detailed subscription view → invoices (with payments) + service incidents."  
    )  
)  
def get_subscription_detail(params: SubscriptionIdParam) -> SubscriptionDetail:  
    """
    Retrieve comprehensive details about a subscription including associated
    product information, billing history (invoices and payments), and
    service incident history.
    
    Args:
        params: SubscriptionIdParam containing the subscription_id to look up
        
    Returns:
        SubscriptionDetail: Complete subscription information including
        product details, invoices with payment history, and service incidents
        
    Raises:
        ValueError: If the subscription ID is not found
    """
    db = get_db()  
    # Get subscription details joined with product information
    sub = db.execute(  
        """  
        SELECT s.*, p.name AS product_name, p.description AS product_description,  
               p.category, p.monthly_fee  
        FROM Subscriptions s  
        JOIN Products p ON p.product_id = s.product_id  
        WHERE s.subscription_id = ?  
        """,  
        (params.subscription_id,),  
    ).fetchone()  
    if not sub:  
        db.close()  
        raise ValueError("Subscription not found")  
  
    # Get all invoices for this subscription and their payments
    invoices_rows = db.execute(  
        """  
        SELECT invoice_id, invoice_date, amount, description, due_date  
        FROM Invoices WHERE subscription_id = ?""",  
        (params.subscription_id,),  
    ).fetchall()  
  
    # Build invoice list with payment details and outstanding amounts
    invoices: List[Invoice] = []  
    for inv in invoices_rows:  
        pay_rows = db.execute(  
            "SELECT * FROM Payments WHERE invoice_id = ?", (inv["invoice_id"],)  
        ).fetchall()  
        # Calculate total successful payments for this invoice
        total_paid = sum(p["amount"] for p in pay_rows if p["status"] == "successful")  
        invoices.append(  
            Invoice(  
                **dict(inv),  
                payments=[Payment(**dict(p)) for p in pay_rows],  
                outstanding=max(inv["amount"] - total_paid, 0.0),  
            )  
        )  
  
    # Get service incident history
    inc_rows = db.execute(  
        """  
        SELECT incident_id, incident_date, description, resolution_status  
        FROM ServiceIncidents  
        WHERE subscription_id = ?""",  
        (params.subscription_id,),  
    ).fetchall()  
  
    db.close()  
    # Combine all information into a SubscriptionDetail object
    return SubscriptionDetail(  
        **dict(sub),  
        invoices=invoices,  
        service_incidents=[ServiceIncident(**dict(r)) for r in inc_rows],  
    )  
  
  
@mcp.tool(description="Return invoice‑level payments list")  
def get_invoice_payments(params: InvoiceIdParam) -> List[Payment]:  
    """
    Retrieve all payments made against a specific invoice.
    
    Args:
        params: InvoiceIdParam containing the invoice_id to look up
        
    Returns:
        List[Payment]: List of all payments made for this invoice
    """
    db = get_db()  
    rows = db.execute("SELECT * FROM Payments WHERE invoice_id = ?", (params.invoice_id,)).fetchall()  
    db.close()  
    return [Payment(**dict(r)) for r in rows]  
  
  
@mcp.tool(description="Record a payment for a given invoice and get new outstanding balance")  
def pay_invoice(invoice_id: int, amount: float, method: str = "credit_card") -> Dict[str, Any]:  
    """
    Record a new payment for an invoice and calculate the remaining balance.
    
    Args:
        invoice_id: ID of the invoice being paid
        amount: Payment amount
        method: Payment method (defaults to "credit_card")
        
    Returns:
        Dict containing the invoice_id and new outstanding balance
        
    Raises:
        ValueError: If the invoice ID is not found
    """
    today = datetime.now().strftime("%Y-%m-%d")  
    db = get_db()  
    # Record the new payment
    db.execute(  
        "INSERT INTO Payments(invoice_id, payment_date, amount, method, status) VALUES (?,?,?,?,?)",  
        (invoice_id, today, amount, method, "successful"),  
    )  
    # Get invoice total amount
    inv = db.execute("SELECT amount FROM Invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()  
    if not inv:  
        db.close()  
        raise ValueError("Invoice not found")  
    # Calculate total successful payments including the new one
    paid = db.execute(  
        "SELECT SUM(amount) as paid FROM Payments WHERE invoice_id = ? AND status='successful'",  
        (invoice_id,),  
    ).fetchone()["paid"]  
    db.commit()  
    db.close()  
    # Calculate remaining balance, ensure it's not negative
    outstanding = max(inv["amount"] - (paid or 0), 0.0)  
    return {"invoice_id": invoice_id, "outstanding": outstanding}  
  
  
@mcp.tool(description="Daily data‑usage records for a subscription over a date range")  
def get_data_usage(  
    subscription_id: int,  
    start_date: str,  
    end_date: str,  
    aggregate: bool = False,  
) -> List[DataUsageRecord] | Dict[str, Any]:  
    """
    Retrieve service usage records for a subscription over a specified date range.
    Can return either daily records or aggregated totals.
    
    Args:
        subscription_id: ID of the subscription to check
        start_date: Beginning of the date range (inclusive)
        end_date: End of the date range (inclusive)
        aggregate: If True, returns totals instead of daily records
        
    Returns:
        Either a list of daily DataUsageRecord objects or a dictionary
        with aggregated totals for the entire period
    """
    db = get_db()  
    # Get daily usage records within the date range
    rows = db.execute(  
        """  
        SELECT usage_date, data_used_mb, voice_minutes, sms_count  
        FROM DataUsage  
        WHERE subscription_id = ?  
          AND usage_date BETWEEN ? AND ?  
        ORDER BY usage_date  
        """,  
        (subscription_id, start_date, end_date),  
    ).fetchall()  
    db.close()  
    
    if aggregate:  
        # Calculate totals across all days
        total_mb = sum(r["data_used_mb"] for r in rows)  
        total_voice = sum(r["voice_minutes"] for r in rows)  
        total_sms = sum(r["sms_count"] for r in rows)  
        return {  
            "subscription_id": subscription_id,  
            "start_date": start_date,  
            "end_date": end_date,  
            "total_mb": total_mb,  
            "total_voice_minutes": total_voice,  
            "total_sms": total_sms,  
        }  
    # Return daily records
    return [DataUsageRecord(**dict(r)) for r in rows]  
  
  
@mcp.tool(description="List every active promotion (no filtering)")  
def get_promotions() -> List[Promotion]:  
    """
    Retrieve all promotions from the database without any filtering.
    
    Returns:
        List[Promotion]: All promotions in the database
    """
    db = get_db()  
    rows = db.execute("SELECT * FROM Promotions").fetchall()  
    db.close()  
    return [Promotion(**dict(r)) for r in rows]  
  
  
@mcp.tool(  
    description="Promotions *eligible* for a given customer right now "  
    "(evaluates basic loyalty/date criteria)."  
)  
def get_eligible_promotions(params: CustomerIdParam) -> List[Promotion]:  
    """
    Find all currently active promotions that a specific customer is eligible for,
    based on their loyalty level and the promotion's validity dates.
    
    Args:
        params: CustomerIdParam containing the customer_id to check eligibility for
        
    Returns:
        List[Promotion]: List of promotions the customer is eligible for
        
    Raises:
        ValueError: If the customer ID is not found
    """
    db = get_db()  
    # Get customer's loyalty level
    cust = db.execute(
        "SELECT loyalty_level FROM Customers WHERE customer_id = ?",
        (params.customer_id,)
    ).fetchone()  
    if not cust:  
        db.close()  
        raise ValueError("Customer not found")  
    loyalty = cust["loyalty_level"]  
    today = datetime.now().strftime("%Y-%m-%d")  
    
    # Get currently active promotions
    rows = db.execute(  
        """  
        SELECT * FROM Promotions  
        WHERE start_date <= ? AND end_date >= ?  
        """,  
        (today, today),  
    ).fetchall()  
    db.close()  
    
    # Filter promotions based on loyalty level criteria
    eligible = []  
    for r in rows:  
        crit = r["eligibility_criteria"] or ""
        # Include if promotion matches loyalty level or has no loyalty requirement
        if f"loyalty_level = '{loyalty}'" in crit or "loyalty_level" not in crit:  
            eligible.append(Promotion(**dict(r)))  
    return eligible  
  
  
# ─── Knowledge Base Search ───────────────────────────────────────────────  
@mcp.tool(description="Semantic search on policy / procedure knowledge documents")  
def search_knowledge_base(params: KBSearchParams) -> List[KBDoc]:  
    """
    Perform semantic search on knowledge base documents using text embeddings.
    This function converts the query into an embedding vector and finds documents
    with similar semantic meaning by comparing embedding vectors.
    
    Args:
        params: KBSearchParams containing:
            - query: Natural language search query
            - topk: Number of most relevant documents to return
            
    Returns:
        List[KBDoc]: Up to topk most semantically relevant documents,
        sorted by relevance (most relevant first)
    """
    # Convert search query to embedding vector
    query_emb = get_embedding(params.query)  
    
    # Get all documents with their embeddings
    db = get_db()  
    rows = db.execute(
        "SELECT title, doc_type, content, topic_embedding FROM KnowledgeDocuments"
    ).fetchall()  
    db.close()  
    
    # Calculate similarity scores for all documents
    scored = []  
    for r in rows:  
        try:  
            # Parse stored embedding vector and compute similarity
            emb = json.loads(r["topic_embedding"])  
            sim = cosine_similarity(query_emb, emb)  
            scored.append((sim, r))  
        except Exception:  
            # Skip documents with invalid embeddings
            continue  
    
    # Sort by similarity score and take top k results
    scored.sort(reverse=True, key=lambda x: x[0])  
    best = scored[: params.topk]  
    
    # Convert to KBDoc objects
    return [  
        KBDoc(title=r["title"], doc_type=r["doc_type"], content=r["content"])  
        for sim, r in best  
    ]  
  
  
# ─── Security Logs ───────────────────────────────────────────────────────  
@mcp.tool(description="Security events for a customer (newest first)")  
def get_security_logs(params: CustomerIdParam) -> List[SecurityLog]:  
    """
    Retrieve security-related events for a specific customer,
    ordered by timestamp from newest to oldest.
    
    Args:
        params: CustomerIdParam containing the customer_id to look up
        
    Returns:
        List[SecurityLog]: Security events for the customer in reverse
        chronological order (newest first)
    """
    db = get_db()  
    rows = db.execute(  
        "SELECT log_id, event_type, event_timestamp, description "  
        "FROM SecurityLogs WHERE customer_id = ? ORDER BY event_timestamp DESC",  
        (params.customer_id,),  
    ).fetchall()  
    db.close()  
    return [SecurityLog(**dict(r)) for r in rows]  
  
  
# ─── Orders ──────────────────────────────────────────────────────────────  
@mcp.tool(description="All orders placed by a customer")  
def get_customer_orders(params: CustomerIdParam) -> List[Order]:  
    """
    Retrieve all orders placed by a specific customer, with product details,
    ordered by date from newest to oldest.
    
    Args:
        params: CustomerIdParam containing the customer_id to look up
        
    Returns:
        List[Order]: All orders for the customer in reverse chronological order
    """
    db = get_db()  
    # Join with Products table to get product names
    rows = db.execute(  
        """  
        SELECT o.order_id, o.order_date, p.name as product_name,  
               o.amount, o.order_status  
        FROM Orders o  
        JOIN Products p ON p.product_id = o.product_id  
        WHERE o.customer_id = ?  
        ORDER BY o.order_date DESC  
        """,  
        (params.customer_id,),  
    ).fetchall()  
    db.close()  
    return [Order(**dict(r)) for r in rows]  
  
  
# ─── Support Tickets ────────────────────────────────────────────────────  
@mcp.tool(description="Retrieve support tickets for a customer (optionally filter by open status)")  
def get_support_tickets(  
    customer_id: int,  
    open_only: bool = False,  
) -> List[SupportTicket]:  
    """
    Retrieve support tickets for a customer, with optional filtering
    to show only open tickets.
    
    Args:
        customer_id: ID of the customer to get tickets for
        open_only: If True, only return tickets that aren't closed
        
    Returns:
        List[SupportTicket]: Support tickets matching the criteria
    """
    db = get_db()  
    # Build query based on open_only flag
    query = "SELECT * FROM SupportTickets WHERE customer_id = ?"  
    if open_only:  
        query += " AND status != 'closed'"  
    rows = db.execute(query, (customer_id,)).fetchall()  
    db.close()  
    return [SupportTicket(**dict(r)) for r in rows]  
  
  
@mcp.tool(description="Create a new support ticket for a customer")  
def create_support_ticket(  
    customer_id: int,  
    subscription_id: int,  
    category: str,  
    priority: str,  
    subject: str,  
    description: str,  
) -> SupportTicket:  
    """
    Create a new support ticket for a customer.
    
    Args:
        customer_id: ID of the customer creating the ticket
        subscription_id: ID of the subscription the ticket is about
        category: Ticket category (e.g., 'technical', 'billing')
        priority: Ticket priority ('high', 'medium', 'low')
        subject: Brief description of the issue
        description: Detailed description of the issue
        
    Returns:
        SupportTicket: The newly created support ticket
    """
    # Get current timestamp for ticket creation
    opened = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  
    db = get_db()  
    
    # Insert new ticket
    cur = db.execute(  
        """  
        INSERT INTO SupportTickets  
        (customer_id, subscription_id, category, opened_at, closed_at,  
         status, priority, subject, description, cs_agent)  
        VALUES (?,?,?,?,?,?,?,?,?,?)  
        """,  
        (  
            customer_id,  
            subscription_id,  
            category,  
            opened,  
            None,          # closed_at is initially None
            "open",        # Initial status
            priority,  
            subject,  
            description,  
            "AI_Bot",      # Ticket created by AI system
        ),  
    )  
    ticket_id = cur.lastrowid  
    db.commit()  
    
    # Retrieve the created ticket to return
    row = db.execute("SELECT * FROM SupportTickets WHERE ticket_id = ?", (ticket_id,)).fetchone()  
    db.close()  
    return SupportTicket(**dict(row))  
  
  
# ─── Products ────────────────────────────────────────────────────────────  
class Product(BaseModel):  
    """
    Product information model.
    Represents a service or product that can be subscribed to or purchased.
    """
    product_id: int  
    name: str  
    description: str  
    category: str       # e.g., 'mobile', 'internet', 'tv'
    monthly_fee: float  # Regular monthly subscription cost
  
  
@mcp.tool(description="List / search available products (optional category filter)")  
def get_products(category: Optional[str] = None) -> List[Product]:  
    """
    Retrieve a list of available products, optionally filtered by category.
    
    Args:
        category: Optional category to filter products by
        
    Returns:
        List[Product]: All products matching the category filter,
        or all products if no category specified
    """
    db = get_db()  
    if category:  
        # Filter products by category
        rows = db.execute("SELECT * FROM Products WHERE category = ?", (category,)).fetchall()  
    else:  
        # Get all products
        rows = db.execute("SELECT * FROM Products").fetchall()  
    db.close()  
    return [Product(**dict(r)) for r in rows]  
  
  
@mcp.tool(description="Return a single product by ID")  
def get_product_detail(product_id: int) -> Product:  
    """
    Retrieve detailed information about a specific product.
    
    Args:
        product_id: ID of the product to look up
        
    Returns:
        Product: Detailed product information
        
    Raises:
        ValueError: If the product ID is not found
    """
    db = get_db()  
    r = db.execute("SELECT * FROM Products WHERE product_id = ?", (product_id,)).fetchone()  
    db.close()  
    if not r:  
        raise ValueError("Product not found")  
    return Product(**dict(r))  
  
  
# ─── Update Subscription ────────────────────────────────────────────────  
@mcp.tool(description="Update one or more mutable fields on a subscription.")  
def update_subscription(subscription_id: int, update: SubscriptionUpdateRequest) -> dict:  
    """
    Update one or more fields of an existing subscription.
    Only fields that are provided in the update request will be modified.
    
    Args:
        subscription_id: ID of the subscription to update
        update: SubscriptionUpdateRequest containing fields to update
        
    Returns:
        dict: Contains subscription_id and list of fields that were updated
        
    Raises:
        ValueError: If no fields are provided to update or if subscription is not found
    """
    # Get only the fields that were actually provided (not None)
    data = update.dict(exclude_unset=True)  
    if not data:  
        raise ValueError("No fields supplied")  
    
    # Build dynamic UPDATE query based on provided fields
    sets = ", ".join(f"{k} = ?" for k in data)  
    params = list(data.values()) + [subscription_id]  
    
    # Execute update
    db = get_db()  
    cur = db.execute(f"UPDATE Subscriptions SET {sets} WHERE subscription_id = ?", params)  
    db.commit()  
    db.close()  
    
    # Check if subscription exists
    if cur.rowcount == 0:  
        raise ValueError("Subscription not found")  
    
    # Return success response with updated fields
    return {
        "subscription_id": subscription_id,
        "updated_fields": list(data.keys())
    }  
  
  
# ─── Unlock Account ──────────────────────────────────────────────────────  
@mcp.tool(description="Unlock a customer account locked for security reasons")  
def unlock_account(params: CustomerIdParam) -> dict:  
    """
    Unlock a customer's account that was previously locked for security reasons.
    This function checks for a recent lock event and creates an unlock event if found.
    
    Args:
        params: CustomerIdParam containing the customer_id to unlock
        
    Returns:
        dict: Success message if account was unlocked
        
    Raises:
        ValueError: If no recent lock event is found for the account
    """
    db = get_db()  
    # Check for recent lock event
    row = db.execute(  
        "SELECT 1 FROM SecurityLogs WHERE customer_id = ? AND event_type = 'account_locked' "  
        "ORDER BY event_timestamp DESC LIMIT 1",  
        (params.customer_id,),  
    ).fetchone()  
    if not row:  
        db.close()  
        raise ValueError("No recent lock event; nothing to do.")  
    
    # Record unlock event
    db.execute(  
        "INSERT INTO SecurityLogs (customer_id, event_type, event_timestamp, description) "  
        "VALUES (?, 'account_unlocked', ?, 'Unlocked via API')",  
        (params.customer_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),  
    )  
    db.commit()  
    db.close()  
    return {"message": "Account unlocked"}  
  
  
# ─── Billing summary ─────────────────────────────────────────────────────  
@mcp.tool(description="What does a customer currently owe across all subscriptions?")  
def get_billing_summary(params: CustomerIdParam) -> Dict[str, Any]:  
    """
    Calculate total outstanding balance for a customer across all their
    subscriptions and invoices.
    
    Args:
        params: CustomerIdParam containing the customer_id to check
        
    Returns:
        Dict containing:
        - customer_id: The customer's ID
        - total_due: Total amount owed across all invoices
        - invoices: List of individual invoice outstanding amounts
    """
    db = get_db()  
    # Get all invoices and their payments for this customer's subscriptions
    inv_rows = db.execute(  
        """  
        SELECT inv.invoice_id, inv.amount,  
               IFNULL(SUM(pay.amount),0) AS paid  
        FROM Invoices inv  
        LEFT JOIN Payments pay ON pay.invoice_id = inv.invoice_id  
                                 AND pay.status='successful'  
        WHERE inv.subscription_id IN  
            (SELECT subscription_id FROM Subscriptions WHERE customer_id = ?)  
        GROUP BY inv.invoice_id  
        """,  
        (params.customer_id,),  
    ).fetchall()  
    db.close()  
    
    # Calculate outstanding amount for each invoice
    outstanding = [  
        {"invoice_id": r["invoice_id"], "outstanding": max(r["amount"] - r["paid"], 0.0)}  
        for r in inv_rows  
    ]  
    
    # Sum up total amount due
    total_due = sum(item["outstanding"] for item in outstanding)  
    
    return {
        "customer_id": params.customer_id,
        "total_due": total_due,
        "invoices": outstanding
    }  
  
  
##############################################################################  
#                                RUN SERVER                                  #  
##############################################################################  
"""
Server startup code. This section is executed when the script is run directly
(not when imported as a module). It starts the FastMCP server in async mode
with Server-Sent Events (SSE) support.
"""

# if __name__ == "__main__":  
#     # Run the MCP server asynchronously on all network interfaces (0.0.0.0)
#     # on port 8000. This makes the API tools accessible via HTTP.
#     asyncio.run(mcp.run_sse_async(host="0.0.0.0", port=8000))  
def create_starlette_app(mcp_server: Server, *, debug: bool = False) -> Starlette:
    """Create a Starlette application that can server the provied mcp server with SSE."""
    sse = SseServerTransport("/messages/")

    async def handle_sse(request: Request) -> None:
        async with sse.connect_sse(
                request.scope,
                request.receive,
                request._send,  # noqa: SLF001
        ) as (read_stream, write_stream):
            await mcp_server.run(
                read_stream,
                write_stream,
                mcp_server.create_initialization_options(),
            )

    return Starlette(
        debug=debug,
        routes=[
            Route("/sse", endpoint=handle_sse),
            Mount("/messages/", app=sse.handle_post_message),
        ],
    )

if __name__ == "__main__":
    mcp_server = mcp._mcp_server  # noqa: WPS437

    import argparse
    
    parser = argparse.ArgumentParser(description='Run MCP SSE-based server')
    parser.add_argument('--host', default='0.0.0.0', help='Host to bind to')
    parser.add_argument('--port', type=int, default=8080, help='Port to listen on')
    args = parser.parse_args()

    # Bind SSE request handling to MCP server
    starlette_app = create_starlette_app(mcp_server, debug=True)

    uvicorn.run(starlette_app, host=args.host, port=args.port)
