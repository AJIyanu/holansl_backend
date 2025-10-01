
# HolanSL Admin Backend

The **HolanSL Admin Backend** is a Django-based backend system designed to streamline and scale the operations of **Holan Integrated Services Limited (HolanSL)**, a fast-growing procurement and logistics company. It provides secure, scalable, and maintainable APIs for managing procurement processes, client-supplier relations, tasks, and financial records.

---

##  About HolanSL

HolanSL specializes in procurement, logistics, and distribution services. Clients rely on HolanSL to **source authentic products at the best price from trusted suppliers**, ensuring they receive the correct specifications and models that fit their needs.

* **How it works:**

  1. A client submits a purchase request.
  2. HolanSL identifies reliable suppliers, negotiates the best price, and ensures product authenticity.
  3. The procured goods are delivered to the client, meeting exact specifications and timelines.

With this model, HolanSL has built trust as a reliable procurement partner. However, rapid expansion revealed limitations in manual and spreadsheet-based tracking methods.

---

##  Problem Statement

* Procurement and deliveries were initially tracked with **notes, emails, and spreadsheets**.
* As the company expanded with more clients, suppliers, and staff, complexity increased.
* Difficulties arose in:

  * Managing user permissions and accountability
  * Assigning and monitoring tasks
  * Reconciling past and present quotes, requests, and payments
* This led to **operational bottlenecks**, productivity drops, and increased risk of errors.

---

##  Solution

To address these challenges, a centralized **web application** was developed.

* **Core Features:**

  * Account ledger for financial reconciliation
  * CRM for clients and suppliers
  * Task management and procurement tracking
  * Role-based permissions for secure collaboration

* **Benefits:**

  * Transparency across all operations
  * Reduced errors and improved efficiency
  * Scalable system to support continued business growth

---

##  Programme Background

This backend system was built as part of the **ALX ProDev Backend Engineering Programme** — an advanced 8-month online programme designed to equip learners with senior backend development expertise.

* **Structure:** 3 months of Professional Foundations + 5 months of Specialized Training
* **Technologies Covered:** Python, JavaScript, SQL, APIs, Microservices, Docker, Kubernetes
* **Approach:** Hands-on projects and real-world applications

---

##  Tech Stack

* **Language:** Python
* **Frameworks:** Django, Django REST Framework (DRF)
* **Database:** PostgreSQL
* **Documentation:** Swagger UI
* **Hosting/Deployment:** Render

 [API Documentation & Schema (Swagger UI)](https://holansl-backend.onrender.com/api/schema/swagger-ui/)

---

##  Features

* User authentication & role-based access
* CRM for managing clients & suppliers
* Procurement tracking & task assignment
* Account ledger for quotes, requests, and payments
* Interactive API documentation with Swagger

---

##  Challenges & Solutions

| **Challenge**                           | **Solution**                                                 |
| --------------------------------------- | ------------------------------------------------------------ |
| Deployment issues with Django on Render | Configured environment variables, PostgreSQL, and Whitenoise |
| Schema evolution without data loss      | Applied staged migrations with backups                       |
| Communicating endpoints to stakeholders | Integrated Swagger UI                                        |
| Enforcing role-based access             | Implemented Django permissions & user groups                 |

---

##  Best Practices

* Modular app structure for maintainability
* Secure environment variable management
* Clear API documentation for collaboration
* Frequent testing with Postman and unit tests
* Deployment integrated into development workflow

---

##  Roadmap

* Docker containerization
* CI/CD pipeline for automated testing & deployment
* Migration toward microservices
* Enhanced monitoring & observability tools

---

## Repository Structure

```bash
holansl-backend/
│
├── api/              # Core APIs  
├── users/            # Authentication & user management  
├── procurement/      # Procurement services  
├── logistics/        # Logistics operations  
├── warehouse/        # Warehouse management  
├── settings/         # Environment-specific configurations  
└── tests/            # Unit and integration tests  
```

---

##  License

This project is developed for **Holan Integrated Services Limited (HolanSL)**.
All rights reserved.

