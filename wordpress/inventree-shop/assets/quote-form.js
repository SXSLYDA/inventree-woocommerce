/**
 * Quote request form: add / remove items, show the call-time box only when "call me" is ticked,
 * and check the "drawing, sample or call" rule before sending. The server checks everything again.
 */
( function () {
	const form = document.querySelector( '.inventree-quote-form' );
	if ( ! form ) return;
	const list = form.querySelector( '.inventree-quote-items' );
	const max = parseInt( list.dataset.max || '25', 10 );

	function renumber() {
		list.querySelectorAll( '.inventree-quote-item' ).forEach( ( item, index ) => {
			item.dataset.index = index;
			const legend = item.querySelector( 'legend' );
			legend.textContent = legend.textContent.replace( /\d+$/, String( index + 1 ) );
			item.querySelectorAll( '[name]' ).forEach( ( el ) => {
				el.name = el.name
					.replace( /^items\[\d+\]/, `items[${ index }]` )
					.replace( /^item_files_\d+/, `item_files_${ index }` );
			} );
			item.querySelector( '.inventree-remove-item' ).hidden = list.children.length === 1;
		} );
	}

	function syncItem( item ) {
		const call = item.querySelector( '.inventree-provide-call' );
		const drawings = item.querySelector( '.inventree-provide-drawings' );
		item.querySelector( '.inventree-call-time' ).hidden = ! call.checked;
		item.querySelector( '.inventree-files' ).hidden = ! drawings.checked;
	}

	form.querySelector( '.inventree-add-item' ).addEventListener( 'click', () => {
		if ( list.children.length >= max ) return;
		const copy = list.lastElementChild.cloneNode( true );
		copy.querySelectorAll( 'input[type=text], input[type=hidden], input[type=file], textarea' ).forEach( ( el ) => { el.value = ''; } );
		copy.querySelector( 'input[type=number]' ).value = '1';
		copy.querySelector( 'select' ).value = 'custom';
		copy.querySelectorAll( 'input[type=checkbox]' ).forEach( ( el ) => { el.checked = el.classList.contains( 'inventree-provide-drawings' ); } );
		list.appendChild( copy );
		renumber();
		syncItem( copy );
		copy.querySelector( 'textarea' ).focus();
	} );

	list.addEventListener( 'click', ( e ) => {
		if ( e.target.classList.contains( 'inventree-remove-item' ) && list.children.length > 1 ) {
			e.target.closest( '.inventree-quote-item' ).remove();
			renumber();
		}
	} );
	list.addEventListener( 'change', ( e ) => {
		const item = e.target.closest( '.inventree-quote-item' );
		if ( item ) syncItem( item );
	} );

	form.addEventListener( 'submit', ( e ) => {
		const problems = [];
		list.querySelectorAll( '.inventree-quote-item' ).forEach( ( item, index ) => {
			const kind = item.querySelector( 'select' ).value;
			const files = item.querySelector( '.inventree-files' ).files.length;
			const sample = item.querySelector( 'input[name$="[provide][sample]"]' ).checked;
			const call = item.querySelector( '.inventree-provide-call' ).checked;
			if ( kind !== 'component' && ! files && ! sample && ! call ) {
				problems.push( `Item ${ index + 1 }: upload a layout drawing or photos, send us a sample, or ask us to call.` );
			}
		} );
		if ( problems.length ) {
			e.preventDefault();
			window.alert( problems.join( '\n' ) );
		}
	} );

	list.querySelectorAll( '.inventree-quote-item' ).forEach( syncItem );
	renumber();
} )();
