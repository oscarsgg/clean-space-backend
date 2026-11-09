from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.employee import Employee
from app.schemas.employee import EmployeeCreate, EmployeeRead, EmployeeUpdate


router = APIRouter(prefix="/employees", tags=["Employees"])


@router.post("/", response_model=EmployeeRead, status_code=status.HTTP_201_CREATED)
def create_employee(employee_data: EmployeeCreate, session: Session = Depends(get_db)):
    employee = Employee(**employee_data.model_dump())
    session.add(employee)
    session.commit()
    session.refresh(employee)
    return employee


@router.get("/", response_model=list[EmployeeRead])
def get_employees(session: Session = Depends(get_db)):
    return session.scalars(select(Employee)).all()


@router.get("/{employee_id}", response_model=EmployeeRead)
def get_employee(employee_id: int, session: Session = Depends(get_db)):
    employee = session.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Empleado no encontrado")
    return employee


@router.put("/{employee_id}", response_model=EmployeeRead)
def update_employee(
    employee_id: int,
    employee_data: EmployeeUpdate,
    session: Session = Depends(get_db),
):
    employee = session.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Empleado no encontrado")
    for field, value in employee_data.model_dump(exclude_unset=True).items():
        setattr(employee, field, value)
    session.commit()
    session.refresh(employee)
    return employee


@router.delete("/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_employee(employee_id: int, session: Session = Depends(get_db)):
    employee = session.get(Employee, employee_id)
    if employee is None:
        raise HTTPException(status_code=404, detail="Empleado no encontrado")
    session.delete(employee)
    session.commit()