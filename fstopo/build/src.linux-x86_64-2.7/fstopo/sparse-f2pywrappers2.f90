!     -*- f90 -*-
!     This file is autogenerated with f2py (version:2)
!     It contains Fortran 90 wrappers to fortran functions.

      subroutine f2pywrap_quicksort_quicksortpartition (array, mid, f2py&
     &_array_d0)
      use quicksort, only : quicksortpartition
      integer mid
      integer f2py_array_d0
      integer array(f2py_array_d0)
      call quicksortpartition(array, mid)
      end subroutine f2pywrap_quicksort_quicksortpartition
      subroutine f2pywrap_quicksort_quicksortarray (array, f2py_array_d0&
     &)
      use quicksort, only : quicksortarray
      integer f2py_array_d0
      integer array(f2py_array_d0)
      call quicksortarray(array)
      end subroutine f2pywrap_quicksort_quicksortarray
      
      subroutine f2pyinitquicksort(f2pysetupfunc)
      interface 
      subroutine f2pywrap_quicksort_quicksortpartition (array, mid, f2py&
     &_array_d0)
      integer mid
      integer f2py_array_d0
      integer array(f2py_array_d0)
      end subroutine f2pywrap_quicksort_quicksortpartition 
      subroutine f2pywrap_quicksort_quicksortarray (array, f2py_array_d0&
     &)
      integer f2py_array_d0
      integer array(f2py_array_d0)
      end subroutine f2pywrap_quicksort_quicksortarray
      end interface
      external f2pysetupfunc
      call f2pysetupfunc(f2pywrap_quicksort_quicksortpartition,f2pywrap_&
     &quicksort_quicksortarray)
      end subroutine f2pyinitquicksort

      
      subroutine f2pyinitprecision(f2pysetupfunc)
      use precision, only : dtype
      external f2pysetupfunc
      call f2pysetupfunc(dtype)
      end subroutine f2pyinitprecision


