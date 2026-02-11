# Backend Optimization Notes

## N+1 Query: `CompanyRepository.get_with_counts()`

**File**: `app/repositories/company_repository.py:32`

**Problem**: Loops through companies and fires 2 extra queries per company (job count + source count). With 50 companies, that's 101 queries instead of 1.

**Fix**: Single query with subquery counts:

```python
async def get_with_counts(self, db: AsyncSession, *, active_only: bool = True, skip: int = 0, limit: int = 50):
    job_count_sq = (
        select(Job.company_id, func.count().label("active_jobs"))
        .where(Job.is_active == True)
        .group_by(Job.company_id)
        .subquery()
    )
    source_count_sq = (
        select(JobSource.company_id, func.count().label("active_sources"))
        .where(JobSource.is_active == True)
        .group_by(JobSource.company_id)
        .subquery()
    )
    query = (
        select(
            Company,
            func.coalesce(job_count_sq.c.active_jobs, 0).label("active_jobs"),
            func.coalesce(source_count_sq.c.active_sources, 0).label("active_sources"),
        )
        .outerjoin(job_count_sq, Company.id == job_count_sq.c.company_id)
        .outerjoin(source_count_sq, Company.id == source_count_sq.c.company_id)
        .order_by(Company.name)
        .offset(skip)
        .limit(limit)
    )
    if active_only:
        query = query.where(Company.is_active == True)
    result = await db.execute(query)
    return [
        {"company": row[0], "active_jobs": row[1], "active_sources": row[2]}
        for row in result.all()
    ]
```

**Impact**: 101 queries → 1 query. Matters once we have 20+ companies.
