library Conditionals version '1.0.0'

// Conditionals. The canonical lowering is UNION ALL of filtered branches partitioned by
// cqlTrue(C) vs not cqlTrue(C) (ARCH-CONDITIONAL): a null condition is not-true and goes
// to the else branch. "NullConditionGoesElse" pins exactly that behavior.

define "SimpleIf": if true then 1 else 2
define "NullConditionGoesElse": if null then 1 else 2
define "FalseConditionGoesElse": if false then 1 else 2
define "CaseWhenSelected":
  case
    when 1 = 2 then 'a'
    when 2 = 2 then 'b'
    else 'c'
  end
define "CaseComparand":
  case 3
    when 1 then 'one'
    when 3 then 'three'
    else 'other'
  end
